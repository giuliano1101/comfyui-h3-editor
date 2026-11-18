"""Local editor sessions and a separate, least-privilege MCP connection identity.

Loopback alone is not authentication. Browser sessions require a same-origin
custom-header bootstrap and an HttpOnly/SameSite cookie. MCP uses an operator-set
bearer secret and an explicit project allowlist; it cannot call legacy write routes.
Modified 2026-11-17 and 2026-11-18, GPL-3.0.
"""
from collections import defaultdict, deque
import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import os
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web


@dataclass(frozen=True)
class Identity:
    name: str
    browser: bool
    projects: frozenset = frozenset()
    edit: bool = False


class Access:
    def __init__(self, clock=time.time):
        write_key,read_key=os.environ.get('H3_MCP_TOKEN',''),os.environ.get('H3_MCP_READ_TOKEN','')
        if len(write_key)>=32 and hmac.compare_digest(write_key,read_key):
            raise ValueError('MCP read and write tokens must be different')
        self.clock, self.sessions = clock, {}
        self.rates = defaultdict(deque)
        self.pending = {}

    @staticmethod
    def error(message="access denied", status=403):
        return web.HTTPForbidden(text=message) if status == 403 else web.HTTPUnauthorized(text=message)

    def local(self, request, navigation=False):
        transport = request.transport
        peer = transport.get_extra_info("peername") if transport else None
        bound = transport.get_extra_info("sockname") if transport else None
        try:
            if not peer or not ipaddress.ip_address(peer[0]).is_loopback:
                raise ValueError()
            # Compare the port with the actual socket, preventing Host/DNS-rebinding tricks.
            authority = urlsplit(request.scheme + "://" + request.host)
            if authority.hostname not in ("localhost", "127.0.0.1", "::1") or authority.username or authority.password or authority.path or authority.query or authority.fragment:
                raise ValueError()
            if (authority.port or (443 if request.scheme == "https" else 80)) != bound[1]:
                raise ValueError()
        except (ValueError, TypeError):
            raise self.error("local editor host required") from None
        # The HTML shell carries no user data. A link from another site may open
        # it; API/session requests still require same-origin checks below.
        if navigation and request.method in ("GET", "HEAD"):
            return
        origin = request.headers.get("Origin")
        if origin and origin != request.scheme + "://" + request.host:
            raise self.error("cross-origin request refused")
        if request.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            raise self.error("cross-origin request refused")

    def throttle(self, identity, category="read", maximum=120):
        now = self.clock()
        bucket = self.rates[(identity, category)]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= maximum:
            raise web.HTTPTooManyRequests(text="connection rate limit reached", headers={"Retry-After": "60"})
        bucket.append(now)

    def bootstrap(self, request):
        self.local(request)
        if request.headers.get("X-H3-Editor") != "1":
            raise self.error()
        self.throttle("session:" + str(request.remote), maximum=30)
        now = self.clock()
        self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
        cookie = request.cookies.get("h3_session", "")
        session = self.sessions.get(cookie)
        if session is None:
            if len(self.sessions) >= 32:
                raise web.HTTPTooManyRequests(text="session capacity reached")
            cookie = secrets.token_urlsafe(32)
            session = (Identity("editor", True, edit=True), now + 8 * 3600)
            self.sessions[cookie] = session
        response = web.json_response({"authenticated": True, "connection": "local-editor", "provider_keys": "server-only"})
        response.headers["Cache-Control"] = "no-store"
        response.set_cookie("h3_session", cookie, httponly=True, samesite="Strict", secure=request.secure, max_age=8 * 3600, path="/h3take")
        return response

    def authenticate(self, request):
        self.local(request)
        authorization = request.headers.get("Authorization", "")
        if authorization:
            key = os.environ.get("H3_MCP_TOKEN", "")
            read_key = os.environ.get("H3_MCP_READ_TOKEN", "")
            write_match = len(key)>=32 and hmac.compare_digest(authorization,"Bearer "+key)
            read_match = len(read_key)>=32 and hmac.compare_digest(authorization,"Bearer "+read_key)
            if not write_match and not read_match:
                raise self.error(status=401)
            # Connection names/allowlists are server facts, never accepted in request JSON.
            projects = frozenset(x.strip() for x in os.environ.get("H3_MCP_PROJECTS", "").split(",") if x.strip())
            identity = Identity("mcp-local" if write_match else "mcp-read-local", False, projects,
                                write_match and os.environ.get("H3_MCP_ALLOW_EDITS") == "1")
        else:
            session = self.sessions.get(request.cookies.get("h3_session", ""))
            if session is None or session[1] <= self.clock():
                raise self.error(status=401)
            identity = session[0]
            if request.method not in ("GET", "HEAD") and request.headers.get("X-H3-Editor") != "1":
                raise self.error("editor request header required")
        self.throttle(identity.name)
        return identity

    def project(self, identity, project):
        if not identity.browser and project not in identity.projects:
            raise self.error("project not allowed for this connection")

    def propose(self, identity, action, payload):
        """An agent can request a preview. Only a real browser session may approve it."""
        now = self.clock()
        self.pending = {k: v for k, v in self.pending.items() if v["expires"] > now}
        if len(self.pending) >= 64:
            raise web.HTTPTooManyRequests(text="approval capacity reached")
        proposal = {"id": secrets.token_urlsafe(24), "owner": identity.name, "action": action,
                    "payload": payload, "expires": now + 300, "approved": False,
                    "requested_by": "browser" if identity.browser else "agent"}
        self.pending[proposal["id"]] = proposal
        try:
            # Remove unexecuted content even if the browser closes and no new
            # proposal arrives. Capture the ID, not a stale dictionary snapshot.
            proposal_id = proposal['id']
            asyncio.get_running_loop().call_later(300, lambda: self.pending.pop(proposal_id, None))
        except RuntimeError:
            pass  # Synchronous callers are pruned by their next operation.
        return {k: v for k, v in proposal.items() if k != "owner"}

    def approve(self, identity, proposal_id):
        if not identity.browser:
            raise self.error("approval requires the local editor")
        proposal = self.pending.get(proposal_id)
        if proposal is None or proposal["expires"] <= self.clock():
            raise web.HTTPConflict(text="proposal expired; prepare it again")
        proposal["approved"] = True
        return {"approved": True}

    def consume(self, identity, proposal_id, action, payload):
        proposal = self.pending.get(proposal_id)
        if not proposal or proposal["expires"] <= self.clock() or not proposal["approved"]:
            raise web.HTTPConflict(text="local editor approval required")
        expected = json_digest(payload)
        if proposal["owner"] != identity.name or proposal["action"] != action or not hmac.compare_digest(json_digest(proposal["payload"]), expected):
            raise self.error("approval does not match this action")
        del self.pending[proposal_id]


def json_digest(value):
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
