"""Non-destructive audio layout and bounded waveform alignment on the existing timeline."""

import numpy as np
import asyncio
from pathlib import Path


def health(api, project):
    missing = []
    for group in ("clips", "effects", "sounds"):
        for entry in project.get(group, []):
            asset = entry if group == "sounds" else api.store.active_take(entry)
            reason = "未生成素材" if not asset else ""
            if asset:
                try:
                    if not Path(
                        api.output_path(asset.get("subfolder", ""), asset["file"])
                    ).is_file():
                        reason = "文件缺失"
                except Exception:
                    reason = "素材路径无效"
            if reason:
                missing.append(
                    {
                        "kind": group,
                        "id": entry["id"],
                        "name": entry.get("name", entry["id"]),
                        "reason": reason,
                    }
                )
    return {
        "revision": project["revision"],
        "missing": missing,
        "export_ready": not missing,
        "notice": "缺失音频或特效时阻止导出，请恢复素材或明确移除对应条目后重试。未使用的镜头缺少素材不影响已完整的时间线。",
    }


def tidy_audio(project):
    sounds = sorted(
        project.get("sounds", []), key=lambda s: (s.get("start", 0), s["id"])
    )
    if len(sounds) > 20:
        raise ValueError("一次最多整理 20 个音频片段，请先分批处理")
    ends = [0] * 4
    ops = []
    for sound in sounds:
        start = sound.get("start", 0)
        lanes = [i for i, end in enumerate(ends) if end <= start]
        if not lanes:
            raise ValueError("存在超过四条同时重叠的音轨，未进行修改")
        lane = next((i for i in lanes if i == sound.get("lane", 0)), lanes[0])
        ends[lane] = start + sound.get("frames", 0)
        if lane != sound.get("lane", 0):
            ops.append(
                {"op": "update_sound", "sound": sound["id"], "fields": {"lane": lane}}
            )
    return ops


def alignment(reference, recording, rate=1000, maximum=2):
    a = np.asarray(reference, dtype=np.float64)
    b = np.asarray(recording, dtype=np.float64)
    if min(len(a), len(b)) < rate // 2:
        raise ValueError("声音过短，无法可靠对齐")
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-8:
        raise ValueError("声音接近静音，无法可靠对齐")
    scores = np.correlate(a, b, "full") / denom
    lags = np.arange(-len(b) + 1, len(a))
    valid = np.abs(lags) <= round(maximum * rate)
    index = int(np.argmax(scores[valid]))
    lag = int(lags[valid][index])
    score = float(scores[valid][index])
    if score < 0.35:
        raise ValueError("波形相似度不足，请手动核对并对齐")
    return {"offset_frames": round(lag / rate * 24), "correlation": round(score, 4)}


async def sync_preview(api, project, item_id, sound_id):
    item = next((i for i in project.get("timeline", []) if i["id"] == item_id), None)
    sound = next((s for s in project.get("sounds", []) if s["id"] == sound_id), None)
    if not item or not sound:
        raise ValueError("请选择时间线片段和音频")
    if item.get("speed", 1) != 1:
        raise ValueError("变速片段请先手动对齐")
    clip = api.store.find_clip(project, item["clip"])
    take = api.store.active_take(clip)
    if not take:
        raise ValueError("参考片段尚无媒体")

    async def pcm(asset, offset):
        path = api.output_path(asset.get("subfolder", ""), asset["file"])
        try:
            raw = await asyncio.wait_for(
                api.run_bytes(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-ss",
                        str(offset),
                        "-i",
                        path,
                        "-t",
                        "10",
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "1000",
                        "-f",
                        "s16le",
                        "-",
                    ]
                ),
                20,
            )
        except asyncio.TimeoutError:
            raise ValueError("音频读取超时，未进行对齐") from None
        return np.frombuffer(raw, dtype="<i2")[:10000]

    reference = await pcm(take, item.get("in", 0) / 24)
    recording = await pcm(sound, sound.get("offset", 0))
    result = alignment(reference, recording)
    start = item["start"] + result["offset_frames"]
    if start < 0:
        raise ValueError("对齐位置早于时间线起点，请先调整音频裁切")
    result["ops"] = [
        {"op": "update_sound", "sound": sound_id, "fields": {"start": start}}
    ]
    return result
