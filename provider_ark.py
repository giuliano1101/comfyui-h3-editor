"""Ark video v3 contract, checked against official docs on 2026-11-18.

Only image references are implemented. Video/audio reference URLs, callbacks,
draft conversion and automatic duration are deliberately not accepted.
"""

import os

try:
    from .provider_jobs import Capabilities, JobError, Poll, remote_handle
    from .provider_transport import SafeHTTPS, approved_host
except ImportError:
    from provider_jobs import Capabilities, JobError, Poll, remote_handle
    from provider_transport import SafeHTTPS, approved_host

MODELS = {
    "doubao-seedance-2-5-260628": {
        "resolutions": ("480p", "720p", "1080p", "4k"),
        "durations": tuple(range(4, 31)),
        "modes": ("text", "image", "reference"),
    },
    "doubao-seedance-2-5-flash-261115": {
        "resolutions": ("480p", "720p"),
        "durations": tuple(range(4, 31)),
        "modes": ("text", "image", "reference"),
    },
    "doubao-seedance-2-0-260128": {
        "resolutions": ("480p", "720p", "1080p"),
        "durations": tuple(range(4, 16)),
        "modes": ("text", "image", "reference"),
    },
}


class ArkAdapter:
    capabilities = Capabilities(
        False,
        False,
        30,
        "火山方舟视频 v3：按服务器配置的币种和最高报价预留。实际费用依账号、像素、时长和输入而异。当前关闭生成音频，仅支持文字/首帧/图片参考。官方 DELETE 还会删除终态记录，故此适配器不执行该接口；停止等待不代表取消或退款。",
        models=tuple(MODELS),
        resolutions=("480p", "720p", "1080p", "4k"),
        durations=tuple(range(4, 31)),
        inputs=("text", "image"),
        modes=("text", "image", "reference"),
        outputs=("video/mp4",),
        auth="server environment bearer",
        protocol="ark-video-v3",
        poll_seconds=10,
        cancellation_scope="official queued-only DELETE; disabled to avoid deleting raced terminal records",
    )
    price_cents = None

    def __init__(
        self,
        *,
        enabled=False,
        credential_env="ARK_API_KEY",
        host="ark.cn-beijing.volces.com",
        download_hosts=(),
        quotes_cents=None,
        models=None,
        transport=None,
    ):
        self.enabled, self.credential_env, self.host = (
            enabled,
            credential_env,
            approved_host(host),
        )
        self.model_capabilities = {
            k: dict(v) for k, v in MODELS.items() if models is None or k in models
        }
        if not self.model_capabilities:
            raise JobError("no implemented models selected")
        self.quotes_cents = dict(quotes_cents or {})
        self.service_label = (
            "火山方舟官方"
            if self.host == "ark.cn-beijing.volces.com"
            else "管理员批准的 Ark v3 兼容中转（未实测）"
        )
        self.api = transport or SafeHTTPS((self.host,))
        self.files = SafeHTTPS(tuple(approved_host(h) for h in download_hosts))

    @property
    def configured(self):
        return bool(os.environ.get(self.credential_env))

    def quote(self, request):
        key = f"{request.model}:{request.resolution}:{request.seconds}"
        return self.quotes_cents.get(
            key + ":" + request.mode,
            self.quotes_cents.get(key) if request.mode == "text" else None,
        )

    def validate(self, request):
        cap = self.model_capabilities.get(request.model)
        if (
            not cap
            or request.resolution not in cap["resolutions"]
            or request.seconds not in cap["durations"]
            or request.mode not in cap["modes"]
        ):
            raise JobError("unsupported Ark model capability")
        count = len(request.media)
        if (
            (request.mode == "text" and count)
            or (request.mode == "image" and count != 1)
            or (request.mode == "reference" and not 1 <= count <= 4)
        ):
            raise JobError("generation references do not match the selected mode")
        if any(
            m.get("role") not in ("first_frame", "reference_image")
            or not m.get("data", "").startswith("data:image/")
            for m in request.media
        ):
            raise JobError("only reviewed project images are accepted")

    def _request(self, method, path, body=None):
        key = os.environ.get(self.credential_env)
        if not key:
            raise JobError("provider credential not configured")
        response = self.api.json(
            method,
            "https://" + self.host + "/api/v3/contents/generations/tasks" + path,
            body=body,
            token=key,
        )
        if response.get("error"):
            raise JobError("provider rejected the request")
        return response

    def submit(self, request):
        self.validate(request)
        content = [{"type": "text", "text": request.prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": m["data"]},
                "role": "first_frame" if request.mode == "image" else "reference_image",
            }
            for m in request.media
        )
        body = {
            "model": request.model,
            "content": content,
            "duration": request.seconds,
            "resolution": request.resolution,
            "ratio": "adaptive" if request.mode == "image" else request.ratio,
            "generate_audio": False,
            "watermark": True,
        }
        if request.mode == "reference":
            body["omni_reference_task_type"] = "auto"
        return remote_handle(self._request("POST", "", body).get("id"))

    def poll(self, handle):
        handle = remote_handle(handle)
        result = self._request("GET", "/" + handle)
        if result.get("id") != handle:
            raise JobError("provider task mismatch")
        state = {
            "queued": "queued",
            "running": "running",
            "succeeded": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
            "expired": "failed",
        }.get(result.get("status"))
        if state is None:
            raise JobError("unknown provider state")
        return Poll(
            state,
            handle
            if state == "succeeded" and result.get("content", {}).get("video_url")
            else "",
        )

    def cancel(self, handle):
        raise JobError(
            "Ark cancellation uses destructive DELETE semantics; automatic deletion is disabled"
        )

    def download(self, handle, target):
        result = self._request("GET", "/" + remote_handle(handle))
        if result.get("id") != handle or result.get("status") != "succeeded":
            raise JobError("provider result unavailable")
        return self.files.download(result.get("content", {}).get("video_url"), target)
