"""Schema-limited tools sharing the editor's locks, revisions and history.

No arbitrary paths, code execution, remote URL inputs or paid render tool. Agent
mutations require both operator permission and a single-use local-editor approval.
Modified 2026-11-17 (GPL-3.0).
"""
import copy
import math

try:
    from .provider_jobs import JobError, identifier
except ImportError:
    from provider_jobs import JobError, identifier


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 128}
LONG_TEXT = {"type": "string", "maxLength": 2000}
NUMBER = {"type": "number", "minimum": -100000, "maximum": 1000000}
FRAME = {"type": "integer", "minimum": 0, "maximum": 1000000}
REVISION = {"type": "integer", "minimum": 1, "maximum": 1000000000}
BOOL = {"type": "boolean"}
FIELDS = {
    "add_clip": {"name": LONG_TEXT, "action": LONG_TEXT, "camera": LONG_TEXT, "dialogue": LONG_TEXT,
                 "sound": LONG_TEXT, "seconds": {"type": "number", "minimum": 2, "maximum": 8}},
    "update_clip": {"name": LONG_TEXT, "action": LONG_TEXT, "camera": LONG_TEXT, "dialogue": LONG_TEXT,
                    "sound": LONG_TEXT, "seconds": {"type": "number", "minimum": 2, "maximum": 8}},
    "set_project": {"name": TEXT, "style": LONG_TEXT, "language": TEXT,
                    "format": {"enum": ["16:9", "9:16", "1:1"]}},
    "update_item": {"start": FRAME, "in": FRAME, "out": FRAME,
                    "speed": {"type": "number", "minimum": 0.25, "maximum": 4},
                    "transition": obj({"type": {"enum": ["none", "dissolve", "black", "wipe"]}, "frames": FRAME}, ("type",))},
    "update_sound": {"start": FRAME, "in": FRAME, "out": FRAME, "lane": FRAME,
                     "gain": {"type": "number", "minimum": 0, "maximum": 4}},
}
OP_SCHEMAS = []
for kind in ("add_clip", "update_clip", "set_project", "update_item", "split_item", "remove_item", "remove_clip", "place_clip", "choose_take", "update_sound"):
    properties = {"op": {"enum": [kind]}, "as": TEXT}
    required = ["op"]
    if kind in FIELDS:
        properties["fields"] = obj(FIELDS[kind])
        required.append("fields")
    if kind in ("update_clip", "remove_clip", "place_clip", "choose_take"):
        properties["clip"] = TEXT
        required.append("clip")
    if kind in ("update_item", "split_item", "remove_item"):
        properties["item"] = TEXT
        required.append("item")
    if kind == "update_sound":
        properties["sound"] = TEXT
        required.append("sound")
    if kind in ("add_clip", "place_clip"):
        properties["start"] = FRAME
    if kind == "add_clip":
        properties["place"] = BOOL
    if kind == "split_item":
        properties["frame"] = FRAME
        required.append("frame")
    if kind in ("update_item", "remove_item"):
        properties["ripple"] = BOOL
    if kind == "choose_take":
        properties["take"] = TEXT
        required.append("take")
    OP_SCHEMAS.append(obj(properties, required))
OPS = {"type": "array", "items": {"oneOf": OP_SCHEMAS}, "minItems": 1, "maxItems": 20}
BASE = {"project": TEXT}
CHANGES = {
    "ops": {"revision": REVISION, "ops": OPS},
    "restore": {"revision": REVISION, "target_revision": REVISION},
    "import": {"revision": REVISION, "job": TEXT, "clip": TEXT},
    "export": {"revision": REVISION},
    "cancel": {"revision": REVISION, "job": TEXT},
}
TOOLS = {}
for name in ("summary", "history", "render_status", "export_plan", "provider_jobs"):
    TOOLS[name] = obj(BASE, ("project",))
for action, fields in CHANGES.items():
    for verb in ("propose", "execute"):
        properties = dict(BASE, **fields)
        if verb == "execute":
            properties["approval"] = TEXT
        TOOLS[verb + "_" + action] = obj(properties, tuple(properties))


def validate(value, schema, depth=0):
    if depth > 12:
        raise JobError("arguments nested too deeply")
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                validate(value, option, depth + 1)
                matches += 1
            except JobError:
                pass
        if matches != 1:
            raise JobError("operation does not match an allowed schema")
        return
    if "enum" in schema and (type(value) is not type(schema["enum"][0]) or value not in schema["enum"]):
        raise JobError("unsupported argument value")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict) or set(value) - schema["properties"].keys() or set(schema["required"]) - value.keys():
            raise JobError("arguments do not match tool schema")
        for key, child in value.items():
            validate(child, schema["properties"][key], depth + 1)
    elif kind == "array":
        if not isinstance(value, list) or not schema["minItems"] <= len(value) <= schema["maxItems"]:
            raise JobError("batch must contain 1 to 20 operations")
        for child in value:
            validate(child, schema["items"], depth + 1)
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema["maxLength"]:
            raise JobError("invalid text argument")
    elif kind in ("number", "integer"):
        if type(value) not in ((int,) if kind == "integer" else (int, float)) or not math.isfinite(value) or not schema["minimum"] <= value <= schema["maximum"]:
            raise JobError("invalid numeric argument")
    elif kind == "boolean" and type(value) is not bool:
        raise JobError("invalid boolean argument")


class Bridge:
    def __init__(self, api, access, manager, staging):
        self.api, self.access, self.manager, self.staging = api, access, manager, staging

    def tools(self, identity):
        return [{"name": name, "description": (
            "Preview a change for local-editor approval; no mutation." if name.startswith("propose_") else
            "Execute exactly the locally approved change once, with a fresh revision." if name.startswith("execute_") else
            "Read the existing editor project. No rendering or billing."), "inputSchema": schema,
            "annotations": {"readOnlyHint": not name.startswith("execute_"), "destructiveHint": name in ("execute_restore", "execute_ops"),
                            "idempotentHint": not name.startswith("execute_"), "openWorldHint": False}}
            for name, schema in TOOLS.items() if identity.edit or not name.startswith(("propose_", "execute_"))]

    def preview(self, action, payload, data):
        store = self.api.store
        if data.get("revision", 0) != payload["revision"]:
            raise JobError("project revision changed; read again", "conflict", 409)
        if action == "ops":
            work, names = copy.deepcopy(data), {}
            try:
                for op in payload["ops"]:
                    store.apply_op(work, op, names)
            except (ValueError, KeyError, TypeError):
                raise JobError("operation rejected; project unchanged") from None
            return work
        if action == "restore":
            old = self.api.read_revision(payload["project"], payload["target_revision"])
            if old is None:
                raise JobError("revision no longer available", "not_found", 404)
            return store.merge(data, old, data)
        if action == "export":
            return store.export_plan(data)
        if action == "cancel":
            self.api.find_job(data, {"job": payload["job"]})
        if action == "import":
            job = self.manager.get(payload["job"], "editor")
            if job["project"] != payload["project"] or job["clip"] != payload["clip"] or not job["staged"]:
                raise JobError("output is not staged for this clip")
            store.find_clip(data, payload["clip"])
        return data

    async def call(self, identity, tool, arguments):
        if tool not in TOOLS:
            raise JobError("unknown tool")
        validate(arguments, TOOLS[tool])
        folder = identifier(arguments["project"], "project")
        if self.api.store.slug(folder) != folder:
            raise JobError("project must be its exact folder identifier")
        self.access.project(identity, folder)
        self.access.throttle(identity.name, "bridge", 60)
        api, store = self.api, self.api.store
        if tool in ("summary", "history", "render_status", "export_plan", "provider_jobs"):
            data = api.read_project(folder)
            if tool == "summary":
                return store.describe(data)
            if tool == "render_status":
                return {"revision": data["revision"], "pending": [{k: j[k] for k in ("id", "clip", "quality") if k in j} for j in data.get("pending", [])]}
            if tool == "history":
                from pathlib import Path
                import json
                rows = []
                for path in sorted(Path(api.history_folder(folder)).glob("*.json"), reverse=True)[:100]:
                    entry = json.loads(path.read_text(encoding="utf-8"))
                    rows.append({"revision": entry["project"]["revision"], "time": entry["time"], "source": entry["source"]})
                return {"revisions": rows}
            if tool == "export_plan":
                try:
                    return {"revision": data["revision"], "plan": store.export_plan(data)}
                except ValueError:
                    raise JobError("export needs rendered media") from None
            return {"jobs": self.manager.list("editor", folder)}
        if not identity.edit:
            raise self.access.error("this connection is read-only")
        verb, action = tool.split("_", 1)
        payload = {k: v for k, v in arguments.items() if k != "approval"}
        async with api.lock(folder):
            data = api.read_project(folder)
            work = self.preview(action, payload, data)
            if verb == "propose":
                result = self.access.propose(identity, action, payload)
                result["notice"] = "Review in the local editor. History restore cannot undo external charges or disclosure."
                return result
            self.access.consume(identity, arguments["approval"], action, payload)
            if action in ("ops", "restore"):
                api.commit(folder, work, "bridge-" + action)
                return {"revision": work["revision"], "summary": store.describe(work)}
            if action == "cancel":
                job = api.find_job(data, {"job": payload["job"]})
                await api.stop_render(data, job)
                api.commit(folder, data, "bridge-cancel")
                return {"revision": data["revision"], "notice": "Local cancellation requested; completed media may still arrive."}
        # Media conversion occurs outside the project lock, then checks revision again.
        if action == "import":
            return await self.staging.import_take(self.manager, payload["job"], "editor", folder, payload["clip"], payload["revision"])
        if action == "export":
            # Export a fixed snapshot rather than silently exporting a newer timeline.
            return await api.export_snapshot(folder, data)
        raise JobError("unsupported operation")
