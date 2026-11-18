"""Provider HTTP routes and bridge lifecycle. All configuration stays server-side."""
import asyncio
import json
import os
from pathlib import Path

from aiohttp import web

from .editor_security import Access
from .editor_bridge import Bridge
from .provider_adapters import configured_adapters
from .provider_jobs import JobError, JobManager, identifier
from .provider_media import Staging
from .provider_service import GenerationService
from .speech import LocalSpeech
from .conversation import Conversation


class Runtime:
    def __init__(self, api, directory, config=None):
        self.api, self.access = api, Access()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if config is None:
            path = Path(os.environ.get("H3_PROVIDER_CONFIG_PATH", directory / "providers.json"))
            if path.exists() and path.stat().st_size > 32768:
                raise JobError("provider config is too large")
            config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        for key, minimum, maximum, default in (("budget_cents", 0, 1000000, 0), ("max_active", 1, 8, 2), ("daily_jobs", 1, 100, 20)):
            value = config.get(key, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise JobError("invalid provider server limits")
        self.manager = JobManager(configured_adapters(config), db_path=directory / "jobs.sqlite3",
                                  budget_cents=config.get("budget_cents", 0), max_active=config.get("max_active", 2),
                                  daily_jobs=config.get("daily_jobs", 20), currency=config.get('currency','USD'))
        self.staging = Staging(directory / "staging", api)
        self.bridge = Bridge(api, self.access, self.manager, self.staging)
        self.generations = GenerationService(api, self.access, self.manager, self.staging)
        self.bridge.generations = self.generations
        self.media_slots = asyncio.Semaphore(2)
        self.speech = LocalSpeech()
        self.conversation = Conversation(api, self.bridge, self.access)

    async def json(self, request, allowed, required=(), limit=32768):
        if request.content_type != "application/json":
            raise JobError("application/json required")
        # Read bounded chunks even when Content-Length is absent/chunked.
        raw = bytearray()
        async for chunk in request.content.iter_chunked(8192):
            raw.extend(chunk)
            if len(raw) > limit:
                raise JobError("request too large", "too_large", 413)
        try:
            body = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise JobError("invalid JSON") from None
        if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - body.keys():
            raise JobError("request does not match endpoint schema")
        return body

    def folder(self, value):
        folder = identifier(value, "project")
        if self.api.store.slug(folder) != folder:
            raise JobError("use the exact project folder identifier")
        return folder

    async def dispatch(self, request, handler):
        try:
            if handler == "provider_session":
                return self.access.bootstrap(request)
            identity = self.access.authenticate(request)
            if not identity.browser:
                if handler not in ("bridge_tools", "bridge_call"):
                    raise self.access.error("MCP connection must use the scoped bridge")
            request["h3_identity"] = identity
            if handler.startswith(("provider_", "bridge_", "speech_", "conversation_")):
                result = await getattr(self, handler)(request, identity)
                response = web.json_response(result)
            else:
                response = await getattr(self.api, handler)(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except JobError as error:
            return web.json_response({"error": str(error), "code": error.code}, status=error.status)
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "invalid request; no provider details disclosed"}, status=400)
        except web.HTTPException:
            raise
        except Exception:
            # Do not allow framework tracebacks to contain provider replies/credentials/prompts.
            return web.json_response({"error": "editor operation failed; inspect local configuration"}, status=500)

    async def provider_capabilities(self, request, identity):
        return {"providers": self.manager.capabilities(), "local_h3": "existing ComfyUI render path",
                "usage": self.manager.usage(identity.name),
                "budget": "lifetime local reservation; not a guarantee of provider charges",
                "voice": self.speech.capabilities()}

    async def speech_capabilities(self, request, identity):
        return self.speech.capabilities()

    async def speech_transcribe(self, request, identity):
        self.access.throttle(identity.name,"asr",10)
        return await self.speech.transcribe(request)

    async def speech_cancel(self, request, identity):
        body=await self.json(request,('request',),('request',))
        return self.speech.cancel(body['request'])

    async def conversation_capabilities(self, request, identity):
        return self.conversation.capabilities()

    async def conversation_preview(self, request, identity):
        self.access.throttle(identity.name,"conversation",20)
        body=await self.json(request,("project","revision","utterance","selected_clip","selected_item","selected_sound","previous","previous_approval","generation"),("project","revision","utterance"))
        return await self.conversation.preview(identity,body)

    async def provider_list(self, request, identity):
        folder = self.folder(request.query.get("project"))
        return {"jobs": self.manager.list(identity.name, folder), "usage": self.manager.usage(identity.name)}

    async def provider_preview(self, request, identity):
        fields = ("provider", "model", "resolution", "prompt", "seconds", "project", "clip", "revision")
        body = await self.json(request, (*fields, "mode", "references"), fields)
        return await self.generations.preview(identity, body)

    async def provider_submit(self, request, identity):
        body = await self.json(request, ("approval", "cost_acknowledged"), ("approval", "cost_acknowledged"))
        return await self.generations.submit(identity, body["approval"], body["cost_acknowledged"])

    async def provider_job(self, request, identity):
        job = identifier(request.match_info["job"], "job")
        action = request.match_info["action"]
        body = await self.json(request, ("handle",))
        self.access.throttle(identity.name, "provider-status", 30)
        if action in ("poll", "cancel"):
            return await asyncio.to_thread(getattr(self.manager, action), job, identity.name)
        if action == "stage":
            async with self.media_slots:
                return {"media": await self.staging.stage(self.manager, job, identity.name)}
        if action == "reconcile" and identity.browser:
            return self.manager.reconcile(job, body.get("handle"), identity.name)
        raise JobError("unknown job action")

    async def provider_approvals(self, request, identity):
        if not identity.browser:
            raise self.access.error()
        return {"proposals": [{k: v for k, v in p.items() if k != "owner"}
            for p in self.access.pending.values() if p["expires"] > self.access.clock()]}

    async def provider_approve(self, request, identity):
        body = await self.json(request, ("approval",), ("approval",))
        if not isinstance(body["approval"], str):
            raise JobError("invalid approval")
        return self.access.approve(identity, body["approval"])

    async def provider_reject(self, request, identity):
        body = await self.json(request, ("approval",), ("approval",))
        if not identity.browser or not isinstance(body["approval"], str):
            raise self.access.error()
        self.access.pending.pop(body["approval"], None)
        return {"rejected": True}

    async def bridge_tools(self, request, identity):
        return {"tools": self.bridge.tools(identity)}

    async def bridge_call(self, request, identity):
        body = await self.json(request, ("name", "arguments"), ("name", "arguments"), limit=524288)
        if not isinstance(body["name"], str):
            raise JobError("invalid tool name")
        async with self.media_slots:
            return await self.bridge.call(identity, body["name"], body["arguments"])


ROUTES = (
    ("GET", "/h3take/api/conversation", "conversation_capabilities"),
    ("POST", "/h3take/api/conversation/preview", "conversation_preview"),
    ("GET", "/h3take/api/speech", "speech_capabilities"),
    ("POST", "/h3take/api/speech/transcribe", "speech_transcribe"),
    ("POST", "/h3take/api/speech/cancel", "speech_cancel"),
    ("GET", "/h3take/api/session", "provider_session"),
    ("GET", "/h3take/api/providers", "provider_capabilities"),
    ("GET", "/h3take/api/provider-jobs", "provider_list"),
    ("POST", "/h3take/api/provider-jobs/preview", "provider_preview"),
    ("POST", "/h3take/api/provider-jobs", "provider_submit"),
    ("POST", "/h3take/api/provider-jobs/{job}/{action}", "provider_job"),
    ("GET", "/h3take/api/approvals", "provider_approvals"),
    ("POST", "/h3take/api/approvals/approve", "provider_approve"),
    ("POST", "/h3take/api/approvals/reject", "provider_reject"),
    ("GET", "/h3take/api/bridge/tools", "bridge_tools"),
    ("POST", "/h3take/api/bridge/call", "bridge_call"),
)
