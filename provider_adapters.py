"""Protocol-specific adapters. MiniMax v1 text-to-video, reviewed 2026-11-17.

The v1 Hailuo endpoint is not the newer H3 v2 API. Neither is an OpenAI API.
No credential or signed URL is retained in the job database or sent to the UI.
"""
import os
from dataclasses import replace
from urllib.parse import urlencode

try:
    from .provider_jobs import Capabilities, JobError, Poll, SimulatedAdapter, remote_handle
    from .provider_transport import SafeHTTPS, approved_host
except ImportError:
    from provider_jobs import Capabilities, JobError, Poll, SimulatedAdapter, remote_handle
    from provider_transport import SafeHTTPS, approved_host


class MiniMaxAdapter:
    capabilities = Capabilities(False, False, 10,
        "MiniMax v1 Hailuo 付费文生/首帧图生视频。按服务器配置的币种和最高报价预留，实际账单可能不同。"
        "未确认官方取消及提交幂等能力；停止本地等待不代表退款。",
        models=("MiniMax-Hailuo-2.3", "MiniMax-Hailuo-02"), resolutions=("768P", "1080P"),
        durations=(6, 10), outputs=("video/mp4",), auth="server environment bearer",
        protocol="minimax-video-v1", poll_seconds=10, inputs=("text", "image"), modes=("text", "image"))

    def __init__(self, *, enabled=False, credential_env="MINIMAX_API_KEY", host="api.minimax.io",
                 download_hosts=(), quotes_cents=None, models=None, transport=None):
        self.enabled, self.credential_env = enabled, credential_env
        self.host = approved_host(host)
        self.service_label = "MiniMax official" if host == "api.minimax.io" else "Administrator-approved relay; compatibility and billing unverified"
        self.download_hosts = tuple(approved_host(h) for h in download_hosts)
        self.price_cents = None
        self.quotes_cents = dict(quotes_cents or {})
        self.model_capabilities = {m: {"resolutions": ("768P", "1080P"), "durations": (6, 10), "modes": ("text", "image"), "max_images": 1}
                                   for m in self.capabilities.models if models is None or m in models}
        # Separate allowlists ensure an API key cannot be sent to a result CDN.
        self.api = transport or SafeHTTPS((self.host,))
        self.files = SafeHTTPS(self.download_hosts)

    @property
    def configured(self):
        return bool(os.environ.get(self.credential_env))

    def quote(self, request):
        key = f"{request.model}:{request.resolution}:{request.seconds}"
        return self.quotes_cents.get(key + ":" + request.mode, self.quotes_cents.get(key) if request.mode == "text" else None)

    def validate(self, request):
        if request.model not in self.capabilities.models or request.resolution not in self.capabilities.resolutions or type(request.seconds) is not int or request.seconds not in self.capabilities.durations:
            raise JobError("unsupported MiniMax v1 model, duration or resolution")
        if request.resolution == "1080P" and request.seconds != 6:
            raise JobError("1080P supports 6 seconds only")
        cap = self.model_capabilities.get(request.model)
        if not cap or any(value not in cap[key] for key, value in (("resolutions", request.resolution), ("durations", request.seconds), ("modes", request.mode))):
            raise JobError("unsupported configured model capability")
        if request.mode == "text" and request.media or request.mode == "image" and len(request.media) != 1:
            raise JobError("generation references do not match the selected mode")

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
        body = {
            "model": request.model, "prompt": request.prompt, "duration": request.seconds,
            "resolution": request.resolution, "prompt_optimizer": False}
        if request.mode == "image":
            image = request.media[0].get("data", "")
            if not image.startswith("data:image/"):
                raise JobError("only reviewed project images are accepted")
            body["first_frame_image"] = image
        result = self._request("POST", "/v1/video_generation", body)
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


def protocol_registry():
    try:
        from .provider_ark import ArkAdapter
    except ImportError:
        from provider_ark import ArkAdapter
    return {"minimax-video-v1": MiniMaxAdapter, "ark-video-v3": ArkAdapter}


def configured_adapters(config):
    """Non-secret config is operator-controlled, never accepted from an HTTP request.

    A relay must explicitly select this implemented protocol and exact endpoint/CDNs.
    No wildcard URL, automatically inferred compatibility or browser key input exists.
    """
    if not isinstance(config, dict) or set(config) - {"providers", "budget_cents", "max_active", "daily_jobs", "currency"} or config.get('currency','USD') not in ('USD','CNY'):
        raise JobError("invalid provider configuration")
    registry = protocol_registry()
    adapters = {"simulation": SimulatedAdapter(), "minimax": MiniMaxAdapter(), "seedance": registry["ark-video-v3"]()}
    providers = config.get("providers", [])
    if not isinstance(providers, list) or len(providers) > 8:
        raise JobError("invalid provider configuration")
    for row in providers:
        if not isinstance(row, dict) or set(row) - {"id", "protocol", "host", "download_hosts", "credential_env", "enabled", "quotes_cents", "models", "model_limits"}:
            raise JobError("invalid provider configuration")
        provider = row.get("id")
        if not isinstance(provider, str) or not provider.isascii() or not provider.replace("-", "").isalnum() or provider == "simulation" or len(provider) > 32:
            raise JobError("invalid provider id")
        if row.get("protocol") not in registry:
            raise JobError("unsupported provider protocol")
        cls = registry[row["protocol"]]
        default_host = "api.minimax.io" if cls is MiniMaxAdapter else "ark.cn-beijing.volces.com"
        if provider == "minimax" and (cls is not MiniMaxAdapter or row.get("host", default_host) != "api.minimax.io"):
            raise JobError("use a separate relay id for a custom endpoint")
        if provider == "seedance" and (cls is MiniMaxAdapter or row.get("host", default_host) != default_host):
            raise JobError("use a separate relay id for a custom protocol or endpoint")
        env = row.get("credential_env", "MINIMAX_API_KEY" if cls is MiniMaxAdapter else "ARK_API_KEY")
        if not isinstance(env, str) or not env.startswith("H3_") and env not in ("MINIMAX_API_KEY", "ARK_API_KEY") or not env.replace("_", "").isalnum() or env.startswith(("H3_MCP_", "H3_EDITOR_")) or not env.endswith("_KEY"):
            raise JobError("invalid credential environment name")
        quotes = row.get("quotes_cents", {})
        implemented = cls().model_capabilities
        allowed_quotes = {f"{model}:{resolution}:{seconds}" + suffix for model, cap in implemented.items()
                          for resolution in cap['resolutions'] for seconds in cap['durations']
                          for suffix in ("", *[":" + m for m in cap['modes']])
                          if cls is not MiniMaxAdapter or resolution != "1080P" or seconds == 6}
        if not isinstance(quotes, dict) or set(quotes) - allowed_quotes or any(type(v) is not int or not 1 <= v <= 100000 for v in quotes.values()):
            raise JobError("invalid provider quotes")
        hosts = row.get("download_hosts", [])
        if not isinstance(hosts, list) or len(hosts) > 16 or type(row.get("enabled", False)) is not bool:
            raise JobError("invalid provider configuration")
        models = row.get("models", list(cls.capabilities.models))
        if not isinstance(models, list) or not models or any(m not in cls.capabilities.models for m in models):
            raise JobError("unknown or empty configured model list")
        adapter = cls(enabled=row.get("enabled", False), credential_env=env,
            host=row.get("host", default_host), download_hosts=hosts, quotes_cents=quotes, models=models)
        limits = row.get("model_limits", {})
        if not isinstance(limits, dict) or set(limits) - set(models): raise JobError("invalid model limits")
        for model, narrowed in limits.items():
            if not isinstance(narrowed, dict) or set(narrowed) - {"modes", "durations", "resolutions"}: raise JobError("invalid model limits")
            for field, values in narrowed.items():
                if not isinstance(values, list) or not values or any(v not in adapter.model_capabilities[model][field] or isinstance(v, bool) for v in values):
                    raise JobError("model limits can only restrict implemented capabilities")
                adapter.model_capabilities[model][field] = tuple(values)
        adapter.capabilities = replace(adapter.capabilities, models=tuple(models))
        adapters[provider] = adapter
    return adapters
