"""Model patches: nodes of any installed pack that take one model and plain values, and return one model.

A project lists the ones it renders with. The editor puts them on the model wire and draws
their controls from the node's own definition, in the shape ComfyUI's GET /object_info gives.
The pack names no other pack's node here, so it works the same with none installed. No Comfy import.
"""

import math
import re

LIMIT = 8
PLAIN = {"INT": "int", "FLOAT": "float", "BOOLEAN": "bool", "STRING": "text"}
# The patches most likely meant for this model list first.
FIRST = re.compile(r"(?<![a-z0-9])h3|minimax|attention|attn", re.IGNORECASE)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def control(name, spec):
    """One input as a control: its name, kind, default and limits. None for an input that is not a plain value."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    kind = spec[0]
    about = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    made = {"name": name}
    if isinstance(about.get("tooltip"), str) and about["tooltip"].strip():
        made["tooltip"] = about["tooltip"].strip()
    if isinstance(kind, (list, tuple)) or kind == "COMBO":
        # A menu is a list of options in the place of the type, or the type COMBO with its options beside it.
        options = list(kind) if isinstance(kind, (list, tuple)) else about.get("options")
        if not isinstance(options, (list, tuple)) or about.get("multiselect") or about.get("multi_select"):
            return None
        if any(not isinstance(option, (str, int, float, bool)) for option in options):
            return None
        default = about.get("default")
        made.update(kind="choice", options=list(options),
                    default=default if chosen(default, options) else options[0] if options else None)
        return made
    if kind not in PLAIN:
        return None
    made["kind"] = PLAIN[kind]
    default = about.get("default")
    if made["kind"] in ("int", "float"):
        for edge in ("min", "max", "step"):
            if number(about.get(edge)):
                made[edge] = about[edge]
        if not number(default):
            # What ComfyUI's own page starts a number at: zero, or the nearest value the input allows.
            default = min(max(0, made.get("min", 0)), made.get("max", math.inf))
        made["default"] = int(default) if made["kind"] == "int" and not (isinstance(default, float) and not default.is_integer()) else default
    elif made["kind"] == "bool":
        made["default"] = default if isinstance(default, bool) else False
    else:
        made["default"] = default if isinstance(default, str) else ""
        if about.get("multiline"):
            made["multiline"] = True
    return made


def chosen(value, options):
    """Whether a value is one of a menu's options. True is not 1 here, though Python says it is."""
    return any(option == value and isinstance(option, bool) == isinstance(value, bool) for option in options)


def pack_of(module):
    """The pack a node comes from, by the module ComfyUI loaded it from: a custom node folder, or ComfyUI itself."""
    module = str(module or "")
    return module[len("custom_nodes."):] if module.startswith("custom_nodes.") and len(module) > 13 else "ComfyUI"


def definition(node_id, info):
    """A node as a patch, or None when it is not one.

    A patch returns exactly one MODEL, takes exactly one MODEL, which it requires, and every
    other input it requires is a number, a switch, text or a menu. Optional inputs of other
    types stay unconnected.
    """
    if not isinstance(info, dict) or list(info.get("output") or []) != ["MODEL"] or any(info.get("output_is_list") or []):
        return None
    inputs = info.get("input") if isinstance(info.get("input"), dict) else {}
    required = inputs.get("required") if isinstance(inputs.get("required"), dict) else {}
    optional = inputs.get("optional") if isinstance(inputs.get("optional"), dict) else {}

    def is_model(spec):
        return isinstance(spec, (list, tuple)) and bool(spec) and spec[0] == "MODEL"

    wires = [name for group in (required, optional) for name, spec in group.items() if is_model(spec)]
    if len(wires) != 1 or wires[0] not in required:
        return None
    controls = []
    for name, spec in required.items():
        if name == wires[0]:
            continue
        made = control(name, spec)
        if made is None:
            return None
        controls.append(made)
    for name, spec in optional.items():
        made = control(name, spec)
        forced = isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) and spec[1].get("forceInput")
        if made is not None and not forced and all(c["name"] != name for c in controls):
            # A plain optional value is a control like the others. Left alone, the node gets its default.
            controls.append(dict(made, optional=True))
    title = info.get("display_name") if isinstance(info.get("display_name"), str) and info["display_name"].strip() else node_id
    made = {"node": node_id, "title": title.strip(), "category": str(info.get("category") or ""),
            "pack": pack_of(info.get("python_module")), "model": wires[0], "controls": controls}
    if isinstance(info.get("description"), str) and info["description"].strip():
        made["about"] = info["description"].strip()[:600]
    return made


def loads_a_lora(known):
    """Whether a patch is a plain LoRA loader: LoRAs have their own list in a project."""
    return "lora" in (known["node"] + " " + known["title"]).lower() and any(c["kind"] == "choice" for c in known["controls"])


def listing(infos, skip=()):
    """Every patch among the installed nodes, the ones for H3 or attention first, then by name.

    infos maps node ids to their definitions. skip holds ids to leave out: the pack's own nodes.
    """
    rows = []
    for node_id, info in infos.items():
        known = None if node_id in skip else definition(node_id, info)
        if known and not loads_a_lora(known):
            rows.append(known)
    return sorted(rows, key=lambda row: (not FIRST.search("%s %s %s" % (row["node"], row["title"], row["category"])),
                                         row["title"].lower(), row["node"]))


def values(row, known):
    """The values a patch's node gets: what the project sets, and the node's defaults for the rest.

    A name the node does not have, or a value outside a control's range or options, is refused
    by name, so a render never runs on something other than what was asked for.
    """
    given = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
    controls = {c["name"]: c for c in known["controls"]}
    unknown = sorted(str(name) for name in set(given) - set(controls))
    if unknown:
        raise ValueError("%s has no input named %s. It takes: %s"
                         % (known["title"], ", ".join(unknown), ", ".join(controls) or "nothing"))
    out = {}
    for name, c in controls.items():
        value = given.get(name, c["default"])
        what = "%s: %s" % (known["title"], name)
        if c["kind"] == "choice":
            if value is None and c.get("optional"):
                continue
            if not c["options"]:
                raise ValueError("%s has nothing to choose from on this machine" % what)
            if not chosen(value, c["options"]):
                shown = ", ".join(str(o) for o in c["options"][:12]) + (", ..." if len(c["options"]) > 12 else "")
                raise ValueError("%s must be one of %s, not %r" % (what, shown, value))
        elif c["kind"] == "bool":
            if not isinstance(value, bool):
                raise ValueError("%s must be true or false, not %r" % (what, value))
        elif c["kind"] == "text":
            if not isinstance(value, str):
                raise ValueError("%s must be text, not %r" % (what, value))
        else:
            if not number(value) or (c["kind"] == "int" and isinstance(value, float) and not value.is_integer()):
                raise ValueError("%s must be a %s, not %r" % (what, "whole number" if c["kind"] == "int" else "number", value))
            if ("min" in c and value < c["min"]) or ("max" in c and value > c["max"]):
                limits = ("from %s to %s" % (c["min"], c["max"]) if "min" in c and "max" in c
                          else "%s or more" % c["min"] if "min" in c else "%s or less" % c["max"])
                raise ValueError("%s must be %s, not %s" % (what, limits, value))
            value = int(value) if c["kind"] == "int" else float(value)
        out[name] = value
    return out
