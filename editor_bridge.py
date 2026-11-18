"""Schema-limited tools sharing the editor's locks, revisions and history.

No arbitrary paths, code execution or remote URL inputs. Paid generation uses the
same material and price approval as the browser. Agent
mutations require both operator permission and a single-use local-editor approval.
Modified 2026-11-17 and 2026-11-18 (GPL-3.0).
"""
import copy
import asyncio
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
    "update_sound": {"start": FRAME, "lane": {"type":"integer","minimum":0,"maximum":3},
                     "gain": {"type": "number", "minimum": 0, "maximum": 2}, "name": TEXT,
                     "offset": {"type": "number", "minimum": 0, "maximum": 36000}, "frames": FRAME},
    "add_subtitle": {"start": FRAME, "end": FRAME, "text": {"type": "string", "minLength": 1, "maxLength": 1000}},
    "update_subtitle": {"start": FRAME, "end": FRAME, "text": {"type": "string", "minLength": 1, "maxLength": 1000}},
}
OP_SCHEMAS = []
for kind in ("add_clip", "update_clip", "set_project", "update_item", "split_item", "remove_item", "remove_clip", "place_clip", "choose_take", "update_sound", "add_subtitle", "update_subtitle", "remove_subtitle"):
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
    if kind in ("update_subtitle", "remove_subtitle"):
        properties["subtitle"] = TEXT
        required.append("subtitle")
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
    "local_render": {"revision": REVISION, "clip": TEXT, "quality": {"enum": ["draft", "final"]}},
    "stage": {"revision": REVISION, "job": TEXT},
    "captions_import": {"revision": REVISION, "replace": BOOL, "cues": {"type": "array", "minItems": 1, "maxItems": 300,
        "items": obj({"id": TEXT, "start": FRAME, "end": FRAME, "text": {"type": "string", "minLength": 1, "maxLength": 1000}}, ("id", "start", "end", "text"))}},
}
TOOLS = {}
for name in ("summary", "history", "render_status", "export_plan", "provider_jobs", "project_health"):
    TOOLS[name] = obj(BASE, ("project",))
for action, fields in CHANGES.items():
    for verb in ("propose", "execute"):
        properties = dict(BASE, **fields)
        if verb == "execute":
            properties["approval"] = TEXT
        TOOLS[verb + "_" + action] = obj(properties, tuple(properties))

GENERATION = {**BASE, "revision": REVISION, "clip": TEXT, "provider": TEXT, "model": TEXT, "resolution": TEXT,
              "prompt": LONG_TEXT, "seconds": {"type": "integer", "minimum": 1, "maximum": 30},
              "mode": {"enum": ["text", "image", "reference"]},
              "references": {"type": "array", "maxItems": 4, "items": obj({"source": {"enum": ["first", "last", "cast"]}, "id": TEXT}, ("source",))}}
TOOLS["propose_generation"] = obj(GENERATION, tuple(k for k in GENERATION if k not in ("mode", "references")))
TOOLS["execute_generation"] = obj({**BASE, "approval": TEXT, "cost_acknowledged": BOOL}, ("project", "approval", "cost_acknowledged"))
TOOLS["provider_status"] = obj({**BASE, "job": TEXT}, ("project", "job"))
TOOLS["provider_capabilities"] = obj(BASE, ("project",))
TOOLS["propose_subtitle_file"] = obj({**BASE,"revision":REVISION,"text":{"type":"string","maxLength":500000},"format":{"enum":["srt","vtt"]},"replace":BOOL},("project","revision","text","format","replace"))
TOOLS["propose_tidy_audio"] = obj({**BASE,"revision":REVISION},("project","revision"))
TOOLS["propose_audio_sync"] = obj({**BASE,"revision":REVISION,"item":TEXT,"sound":TEXT},("project","revision","item","sound"))
for verb in ("propose", "execute"):
    fields={**BASE, "revision": REVISION, "job": TEXT}
    if verb=="execute": fields["approval"]=TEXT
    TOOLS[verb+"_provider_cancel"]=obj(fields,tuple(fields))


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
        if not isinstance(value, list) or not schema.get("minItems",0) <= len(value) <= schema.get("maxItems",300):
            raise JobError("batch must contain 1 to 20 operations")
        for child in value:
            validate(child, schema["items"], depth + 1)
    elif kind == "string":
        if not isinstance(value, str) or not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength",2000):
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
                            "idempotentHint": not name.startswith("execute_"),
                            "openWorldHint": any(part in name for part in ('generation','provider_status','stage'))}}
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
        if action == "captions_import":
            from . import captions
            work=copy.deepcopy(data)
            cues=[] if payload["replace"] else work.get("subtitles",[])
            for cue in payload["cues"]:
                checked=captions.clean({k:v for k,v in cue.items() if k!='id'})
                if checked['end']<=checked['start'] or not cue['id'].startswith('sub') or any(c['id']==cue['id'] for c in cues):
                    raise JobError("invalid or duplicate subtitle")
                cues.append(copy.deepcopy(cue))
            if len(cues)>300: raise JobError("at most 300 subtitles per project")
            work['subtitles']=cues
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
        if action == "local_render":
            self.api.store.find_clip(data, payload["clip"])
        if action in ("provider_cancel", "stage"):
            job = self.manager.get(payload["job"], "editor")
            if job["project"] != payload["project"]:
                raise self.access.error("job is outside this project")
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
        if tool == "provider_capabilities":
            return {"providers": self.manager.capabilities()}
        if tool == "provider_status":
            job = self.manager.get(arguments["job"], "editor")
            if job["project"] != folder:
                raise self.access.error("job is outside this project")
            return await asyncio.to_thread(self.manager.poll, arguments["job"], "editor")
        if tool in ("summary", "history", "render_status", "export_plan", "provider_jobs", "project_health"):
            data = api.read_project(folder)
            if tool == "project_health":
                from .editing_tools import health
                return health(api, data)
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
        if tool == "propose_subtitle_file":
            from . import captions
            try: cues=captions.parse(arguments['text'],arguments['format'])
            except ValueError as error: raise JobError(str(error)) from None
            return await self.call(identity,"propose_captions_import",{"project":folder,"revision":arguments['revision'],"replace":arguments['replace'],"cues":cues})
        if tool in ("propose_tidy_audio","propose_audio_sync"):
            from . import editing_tools
            data=api.read_project(folder)
            try:
                ops=editing_tools.tidy_audio(data) if tool=="propose_tidy_audio" else (await editing_tools.sync_preview(api,data,arguments['item'],arguments['sound']))['ops']
            except ValueError as error: raise JobError(str(error)) from None
            if not ops: return {"unchanged":True,"notice":"音轨已经整理，无需修改。"}
            return await self.call(identity,"propose_ops",{"project":folder,"revision":arguments['revision'],"ops":ops})
        if tool == "propose_generation":
            return await self.generations.preview(identity, arguments)
        if tool == "execute_generation":
            proposal = self.access.pending.get(arguments["approval"])
            if not proposal or proposal["payload"].get("project") != folder:
                raise JobError("approval is outside this project")
            return await self.generations.submit(identity, arguments["approval"], arguments["cost_acknowledged"])
        verb, action = tool.split("_", 1)
        payload = {k: v for k, v in arguments.items() if k != "approval"}
        async with api.lock(folder):
            data = api.read_project(folder)
            work = self.preview(action, payload, data)
            if verb == "propose":
                result = self.access.propose(identity, action, payload)
                if action == 'export':
                    result['export_plan']=work
                    self.access.pending[result['id']]['export_plan']=work
                result["notice"] = "Review in the local editor. History restore cannot undo external charges or disclosure."
                if action == "local_render":
                    result["notice"] = "本地 GPU 渲染会使用当前 H3 模型与显存，不调用收费 API。请核对镜头和画质。取消不保证立即释放所有资源。"
                return result
            self.access.consume(identity, arguments["approval"], action, payload)
            if action in ("ops", "restore", "captions_import"):
                api.commit(folder, work, "bridge-" + action)
                return {"revision": work["revision"], "summary": store.describe(work)}
            if action == "cancel":
                job = api.find_job(data, {"job": payload["job"]})
                await api.stop_render(data, job)
                api.commit(folder, data, "bridge-cancel")
                return {"revision": data["revision"], "notice": "Local cancellation requested; completed media may still arrive."}
            if action == "local_render":
                job = await api.start_render(data, folder, payload["clip"], payload["quality"], None, None, None, None)
                api.commit(folder, data, "bridge-local-render")
                return {"job": job, "revision": data["revision"]}
        # Media conversion occurs outside the project lock, then checks revision again.
        if action == "import":
            return await self.staging.import_take(self.manager, payload["job"], "editor", folder, payload["clip"], payload["revision"])
        if action == "export":
            # Export a fixed snapshot rather than silently exporting a newer timeline.
            import json
            response = await api.export_snapshot(folder, data)
            return json.loads(response.body)
        if action == "stage":
            return {"media": await self.staging.stage(self.manager, payload["job"], "editor")}
        if action == "provider_cancel":
            return await asyncio.to_thread(self.manager.cancel, payload["job"], "editor")
        raise JobError("unsupported operation")
