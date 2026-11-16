"""Protocol-specific adapters. MiniMax v1 text-to-video, reviewed 2026-11-17.

The v1 Hailuo endpoint is not the newer H3 v2 API. Neither is an OpenAI API.
No credential or signed URL is retained in the job database or sent to the UI.
"""
import os
from urllib.parse import urlencode

try:
    from .provider_jobs import Capabilities, JobError, Poll, SimulatedAdapter, remote_handle
    from .provider_transport import SafeHTTPS, approved_host
except ImportError:
    from provider_jobs import Capabilities, JobError, Poll, SimulatedAdapter, remote_handle
    from provider_transport import SafeHTTPS, approved_host


class MiniMaxAdapter:
    capabilities = Capabilities(False, False, 10,
        "Paid MiniMax v1 Hailuo text-to-video. Server-configured quote in USD; actual billing can differ. "
        "No documented cancellation or submission idempotency. Cancelling a local wait is not a refund.",
        models=("MiniMax-Hailuo-2.3", "MiniMax-Hailuo-02"), resolutions=("768P", "1080P"),
        durations=(6, 10), outputs=("video/mp4",), auth="server environment bearer",
        protocol="minimax-video-v1", poll_seconds=10)

    def __init__(self, *, enabled=False, credential_env="MINIMAX_API_KEY", host="api.minimax.io",
                 download_hosts=(), quotes_cents=None, transport=None):
        self.enabled, self.credential_env = enabled, credential_env
        self.host = approved_host(host)
        self.service_label = "MiniMax official" if host == "api.minimax.io" else "Administrator-approved relay; compatibility and billing unverified"
        self.download_hosts = tuple(approved_host(h) for h in download_hosts)
        self.price_cents = None
        self.quotes_cents = dict(quotes_cents or {})
        # Separate allowlists ensure an API key cannot be sent to a result CDN.
        self.api = transport or SafeHTTPS((self.host,))
        self.files = SafeHTTPS(self.download_hosts)

    @property
    def configured(self):
        return bool(os.environ.get(self.credential_env))

    def quote(self, request):
        return self.quotes_cents.get(f"{request.model}:{request.resolution}:{request.seconds}")

    def validate(self, request):
        if request.model not in self.capabilities.models or request.resolution not in self.capabilities.resolutions or type(request.seconds) is not int or request.seconds not in self.capabilities.durations:
            raise JobError("unsupported MiniMax v1 model, duration or resolution")
        if request.resolution == "1080P" and request.seconds != 6:
            raise JobError("1080P supports 6 seconds only")

    def _request(self, method, path, body=None):
        key = os.environ.get(self.credential_env)
        if not key:
            raise JobError("provider credential not configured")
        response = self.api.json(method, "https://" + self.host + path, body=body, token=key)
        base = response.get("base_resp")
        if not isinstance(base, dict) or type(base.get("status_code")) is not int or base["status_code"] != 0:
            raise JobError("provider rejected the request")
        return response

    def submit(self, request):
        self.validate(request)
        result = self._request("POST", "/v1/video_generation", {
            "model": request.model, "prompt": request.prompt, "duration": request.seconds,
            "resolution": request.resolution, "prompt_optimizer": False})
        return remote_handle(result.get("task_id"))

    def poll(self, handle):
        handle = remote_handle(handle)
        data = self._request("GET", "/v1/query/video_generation?" + urlencode({"task_id": handle}))
        if data.get("task_id") != handle:
            raise JobError("provider task mismatch")
        states = {"Preparing": "queued", "Queueing": "queued", "Processing": "running", "Success": "succeeded", "Fail": "failed"}
        state = states.get(data.get("status"))
        if state is None:
            raise JobError("unknown provider state")
        return Poll(state, remote_handle(data.get("file_id")) if state == "succeeded" else "")

    def cancel(self, handle):
        raise JobError("MiniMax v1 has no documented cancellation endpoint")

    def download(self, file_id, target):
        data = self._request("GET", "/v1/files/retrieve?" + urlencode({"file_id": remote_handle(file_id)}))
        file = data.get("file") or {}
        if str(file.get("file_id")) != file_id or file.get("purpose") != "video_generation":
            raise JobError("provider file mismatch")
        # Retrieve again after a restart; never persist the short-lived download URL.
        return self.files.download(file.get("download_url"), target)


def configured_adapters(config):
    """Non-secret config is operator-controlled, never accepted from an HTTP request.

    A relay must explicitly select this implemented protocol and exact endpoint/CDNs.
    No wildcard URL, automatically inferred compatibility or browser key input exists.
    """
    if not isinstance(config, dict) or set(config) - {"providers", "budget_cents", "max_active", "daily_jobs"}:
        raise JobError("invalid provider configuration")
    adapters = {"simulation": SimulatedAdapter(), "minimax": MiniMaxAdapter()}
    providers = config.get("providers", [])
    if not isinstance(providers, list) or len(providers) > 8:
        raise JobError("invalid provider configuration")
    for row in providers:
        if not isinstance(row, dict) or set(row) - {"id", "protocol", "host", "download_hosts", "credential_env", "enabled", "quotes_cents"}:
            raise JobError("invalid provider configuration")
        provider = row.get("id")
        if not isinstance(provider, str) or not provider.isascii() or not provider.replace("-", "").isalnum() or provider == "simulation" or len(provider) > 32:
            raise JobError("invalid provider id")
        if row.get("protocol") != "minimax-video-v1":
            raise JobError("unsupported provider protocol")
        if provider == "minimax" and row.get("host", "api.minimax.io") != "api.minimax.io":
            raise JobError("use a separate relay id for a custom endpoint")
        env = row.get("credential_env", "MINIMAX_API_KEY")
        if not isinstance(env, str) or not env.startswith("H3_") and env != "MINIMAX_API_KEY" or not env.replace("_", "").isalnum() or env.startswith(("H3_MCP_", "H3_EDITOR_")) or not env.endswith("_KEY"):
            raise JobError("invalid credential environment name")
        quotes = row.get("quotes_cents", {})
        allowed_quotes = {f"{model}:{resolution}:{seconds}" for model in MiniMaxAdapter.capabilities.models
                          for resolution in ("768P", "1080P") for seconds in ((6,) if resolution == "1080P" else (6, 10))}
        if not isinstance(quotes, dict) or set(quotes) - allowed_quotes or any(type(v) is not int or not 1 <= v <= 100000 for v in quotes.values()):
            raise JobError("invalid provider quotes")
        hosts = row.get("download_hosts", [])
        if not isinstance(hosts, list) or len(hosts) > 16 or type(row.get("enabled", False)) is not bool:
            raise JobError("invalid provider configuration")
        adapters[provider] = MiniMaxAdapter(enabled=row.get("enabled", False), credential_env=env,
            host=row.get("host", "api.minimax.io"), download_hosts=hosts, quotes_cents=quotes)
    return adapters
