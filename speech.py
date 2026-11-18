"""Explicit loopback upload; transient audio files are removed when the request exits."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import re

from .provider_jobs import JobError


class LocalSpeech:
    def __init__(self):
        self.slots = asyncio.Semaphore(1)
        self.requests = {}

    def cancel(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[a-f0-9-]{36}", request_id
        ):
            raise JobError("转写请求标识无效")
        task = self.requests.get(request_id)
        if task:
            task.cancel()
        return {"cancel_requested": bool(task), "audio_retained": False}

    def capabilities(self):
        model = Path(os.environ.get("H3_ASR_MODEL", "__not_configured__"))
        configured = (
            model.is_dir()
            and (model / "model.bin").is_file()
            and (model / "config.json").is_file()
        )
        return {
            "configured": configured,
            "backend": "faster-whisper / CPU INT8",
            "network": "local-only",
            "max_seconds": 30,
            "retention": "音频只保留在本机临时目录，完成、失败或取消后删除；文本仅留当前标签页，应用修改后才进入项目历史。",
        }

    async def transcribe(self, request):
        request_id = request.headers.get("X-H3-ASR-Request", "")
        if (
            not re.fullmatch(r"[a-f0-9-]{36}", request_id)
            or request_id in self.requests
        ):
            raise JobError("转写请求标识无效或重复")
        if len(self.requests) >= 2:
            raise JobError("本地转写正在处理其他音频", "busy", 429)
        self.requests[request_id] = asyncio.current_task()
        try:
            return await self._transcribe(request)
        except asyncio.CancelledError:
            raise JobError("本次转写已取消", "cancelled", 409) from None
        finally:
            self.requests.pop(request_id, None)

    async def _transcribe(self, request):
        if request.headers.get("X-H3-Local-Audio-Consent") != "1":
            raise JobError("请先确认仅向本机转写器发送音频", "consent_required", 409)
        if not self.capabilities()["configured"]:
            raise JobError("本地转写模型尚未配置", "unavailable", 409)
        if request.content_type not in (
            "audio/webm",
            "audio/wav",
            "audio/x-wav",
            "audio/mpeg",
            "audio/mp4",
            "audio/ogg",
            "application/octet-stream",
        ):
            raise JobError("请选择受支持的音频文件")
        body = bytearray()
        async for chunk in request.content.iter_chunked(65536):
            body.extend(chunk)
            if len(body) > 8 << 20:
                raise JobError("音频超过 8 MiB 上限", "too_large", 413)
        if not body:
            raise JobError("音频为空")
        async with self.slots:
            with tempfile.TemporaryDirectory(prefix="h3-local-asr-") as directory:
                audio = Path(directory) / "audio.bin"
                audio.write_bytes(body)
                env = {
                    k: v
                    for k, v in os.environ.items()
                    if not any(
                        word in k.upper()
                        for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PROXY")
                    )
                }
                env.update(
                    HF_HUB_OFFLINE="1",
                    HF_HUB_DISABLE_TELEMETRY="1",
                    PYTHONIOENCODING="utf-8",
                )
                process = await asyncio.create_subprocess_exec(
                    os.environ.get("H3_ASR_PYTHON", sys.executable),
                    "-B",
                    str(Path(__file__).parent / "tools/transcribe_local.py"),
                    "--model",
                    os.environ["H3_ASR_MODEL"],
                    "--audio",
                    str(audio),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                try:
                    stdout, _ = await asyncio.wait_for(process.communicate(), 60)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    raise
                if process.returncode or len(stdout) > 32768:
                    raise JobError(
                        "本地转写失败；请检查模型、音频长度及可选依赖",
                        "asr_failed",
                        422,
                    )
                try:
                    result = json.loads(stdout)
                except (ValueError, UnicodeError):
                    raise JobError("本地转写回复无效") from None
                return {**result, "network": "local-only", "audio_retained": False}
