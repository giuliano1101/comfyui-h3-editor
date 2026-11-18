import concurrent.futures
import json
import subprocess
import uuid
from pathlib import Path
import av
import imageio_ffmpeg
import requests

repo = Path(__file__).resolve().parents[2]
base = repo / ".acceptance"
url = "http://127.0.0.1:8190/h3take/api"
session = requests.Session()
session.trust_env = False
session.headers["X-H3-Editor"] = "1"


def req(method, path, body=None, **kwargs):
    r = session.request(method, url + path, json=body, timeout=90, **kwargs)
    if r.status_code != 200:
        raise RuntimeError(path + ": HTTP " + str(r.status_code) + " " + r.text[:300])
    return r.json()


req("GET", "/session")
providers = req("GET", "/providers")
folder = "H3_综合验收_20261118_2"


def export(_):
    client = requests.Session()
    client.trust_env = False
    client.cookies.update(session.cookies)
    client.headers["X-H3-Editor"] = "1"
    r = client.post(
        url + "/project/" + folder + "/export",
        json={"name": "最终隔离验收"},
        timeout=90,
    )
    r.raise_for_status()
    result = r.json()
    path = base / "ComfyUI/output" / result["subfolder"] / result["filename"]
    with av.open(str(path)) as container:
        result["decoded_frames"] = len(list(container.decode(video=0)))
        result["audio_streams"] = len(container.streams.audio)
    return result


with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    exports = list(pool.map(export, range(2)))
state = req("GET", "/project/H3_综合验收_20261118")
clip = state["clips"][0]["id"]
before = req("GET", "/provider-jobs?project=H3_综合验收_20261118")
preview = req(
    "POST",
    "/conversation/preview",
    {
        "project": "H3_综合验收_20261118",
        "revision": state["revision"],
        "utterance": "用所选供应商为当前镜头生成4秒视频，只做费用预览",
        "selected_clip": clip,
        "generation": {
            "provider": "simulation",
            "model": "simulation",
            "resolution": "test",
            "seconds": 6,
            "prompt": "纸船漂浮在水上，固定镜头",
            "mode": "text",
            "references": [],
        },
    },
)
if preview.get("proposal"):
    req("POST", "/approvals/reject", {"approval": preview["proposal"]["id"]})
after = req("GET", "/provider-jobs?project=H3_综合验收_20261118")
webm = base / "command-zh.webm"
subprocess.run(
    [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-v",
        "error",
        "-i",
        str(base / "command-zh.wav"),
        "-c:a",
        "libopus",
        str(webm),
    ],
    check=True,
)
transcript = req(
    "POST",
    "/speech/transcribe",
    data=webm.read_bytes(),
    headers={
        "Content-Type": "audio/webm; codecs=opus",
        "X-H3-Local-Audio-Consent": "1",
        "X-H3-ASR-Request": str(uuid.uuid4()),
    },
)
queue = session.get("http://127.0.0.1:8190/queue", timeout=10).json()
stats = session.get("http://127.0.0.1:8190/system_stats", timeout=10).json()
report = {
    "environment": "final real isolated ComfyUI",
    "comfyui_version": stats["system"]["comfyui_version"],
    "real_providers": {
        k: {
            "enabled": v["enabled"],
            "configured": v["configured"],
            "quote_currency": v["quote_currency"],
        }
        for k, v in providers["providers"].items()
        if not v["simulated"]
    },
    "parallel_exports": exports,
    "distinct_export_filenames": len({r["filename"] for r in exports}) == 2,
    "explicit_duration_preview": {
        "draft": preview["draft"],
        "seconds": preview.get("proposal", {}).get("payload", {}).get("seconds"),
        "simulated": preview.get("proposal", {}).get("simulated"),
        "executed": preview["executed"],
        "job_count_unchanged": len(before["jobs"]) == len(after["jobs"]),
    },
    "synthetic_webm_transcription": transcript,
    "asr_quality": "not passed; synthetic speech still needs correction; not microphone",
    "queue_running": len(queue["queue_running"]),
    "queue_pending": len(queue["queue_pending"]),
    "usage": after["usage"],
}
report["passed"] = (
    report["distinct_export_filenames"]
    and all(r["decoded_frames"] == 89 and r["audio_streams"] == 1 for r in exports)
    and preview.get("proposal", {}).get("payload", {}).get("seconds") == 4
    and not preview["executed"]
    and report["explicit_duration_preview"]["job_count_unchanged"]
    and all(not v["enabled"] for v in report["real_providers"].values())
)
(repo / "docs/acceptance/20261118/final-deployment-smoke.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "passed": report["passed"],
            "distinct_exports": report["distinct_export_filenames"],
            "explicit_seconds": report["explicit_duration_preview"]["seconds"],
            "queue_running": report["queue_running"],
            "queue_pending": report["queue_pending"],
            "real_api_enabled": any(
                v["enabled"] for v in report["real_providers"].values()
            ),
        }
    )
)
