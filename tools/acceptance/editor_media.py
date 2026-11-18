import hashlib
import json
import shutil
import subprocess
import time
import wave
from pathlib import Path
import numpy as np
import requests
import imageio_ffmpeg
import av

repo = Path(__file__).resolve().parents[2]
base = repo / ".acceptance"
root = base / "ComfyUI"
folder = "H3_综合验收_20261118"
session = requests.Session()
session.trust_env = False
session.headers["X-H3-Editor"] = "1"
report = {
    "environment": "real ComfyUI 0.45.0; isolated output; synthetic media",
    "project": folder,
    "checks": [],
}


def record(name, **facts):
    report["checks"].append({"name": name, **facts})
    (base / "actual-editor.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def req(method, path, body=None, **kwargs):
    response = session.request(
        method,
        "http://127.0.0.1:8190/h3take/api" + path,
        json=body,
        timeout=180,
        **kwargs,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{path}: {response.status_code} " + response.text[:500])
    return response.json()


def data():
    return req("GET", "/project/" + folder)


def bridge(name, args):
    return req("POST", "/bridge/call", {"name": name, "arguments": args})


def apply(proposal):
    req("POST", "/approvals/approve", {"approval": proposal["id"]})
    return bridge(
        "execute_" + proposal["action"],
        {**proposal["payload"], "approval": proposal["id"]},
    )


req("GET", "/session")
if folder in req("GET", "/projects")["projects"]:
    raise RuntimeError(
        "test project already exists: preserve it; choose a new explicit fixture name"
    )
req("POST", "/projects", {"name": folder})
state = data()
state["sizes"] = {"final": "small"}
req("PUT", "/project/" + folder, state)
ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
rate = 48000
t = np.arange(4 * rate) / rate
audio = 0.25 * np.sin(2 * np.pi * (220 * t + 80 * t * t)) * (np.mod(t, 0.8) < 0.3)


def wav(path, samples):
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        out.writeframes((samples * 32767).astype("<i2").tobytes())


wav(base / "reference.wav", audio)
wav(base / "shifted.wav", audio[rate // 2 :])
video = base / "synthetic.mp4"
subprocess.run(
    [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x352:rate=24:duration=4",
        "-i",
        str(base / "reference.wav"),
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(video),
    ],
    check=True,
)
with video.open("rb") as file:
    result = req(
        "POST",
        "/project/" + folder + "/import",
        files={"file": ("测试画面.mp4", file, "video/mp4")},
        data={"name": "合成测试片段"},
    )
record(
    "media_import",
    revision=result["revision"],
    frames=result["frames"],
    clip=result["clip"],
    source_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
)
state = data()
# ROOT is taken from the server's imported take, never guessed from a separate editor.
take = state["clips"][0]["takes"][0]
project_subfolder = str(Path(take["subfolder"]).parent).replace("\\", "/")
assetdir = root / "output" / project_subfolder / "assets"
assetdir.mkdir(parents=True, exist_ok=True)
shutil.copy2(base / "shifted.wav", assetdir / "sync.wav")
req(
    "POST",
    "/project/" + folder + "/ops",
    {
        "revision": state["revision"],
        "ops": [
            {
                "op": "add_sound",
                "asset": {
                    "file": "sync.wav",
                    "subfolder": project_subfolder + "/assets",
                },
                "seconds": 3.5,
                "fields": {
                    "name": "偏移半秒的测试音轨",
                    "start": 0,
                    "gain": 0.5,
                    "lane": 0,
                },
            }
        ],
    },
)
state = data()
proposal = bridge(
    "propose_audio_sync",
    {
        "project": folder,
        "revision": state["revision"],
        "item": state["timeline"][0]["id"],
        "sound": state["sounds"][0]["id"],
    },
)
apply(proposal)
state = data()
record(
    "waveform_sync",
    start_frames=state["sounds"][0]["start"],
    expected_frames=12,
    passed=state["sounds"][0]["start"] == 12,
)
proposal = bridge(
    "propose_subtitle_file",
    {
        "project": folder,
        "revision": state["revision"],
        "text": "1\n00:00:00,500 --> 00:00:02,000\n中文剪辑验收\n\n2\n00:00:02,000 --> 00:00:03,500\n字幕、音轨与工程恢复",
        "format": "srt",
        "replace": False,
    },
)
apply(proposal)
state = data()
record("subtitle_import", count=len(state["subtitles"]), revision=state["revision"])
health = bridge("project_health", {"project": folder})
record("health", **health)
export = req(
    "POST",
    "/project/" + folder + "/export",
    {"quality": "high", "name": "中文验收成片"},
)
output = root / "output" / export["subfolder"] / export["filename"]
record(
    "export",
    **export,
    sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
    path=str(output.relative_to(base)),
)
# Decode output and compare actual subtitle-bearing frame against the normalized take.

with av.open(str(output)) as container:
    frames = list(container.decode(video=0))
    record(
        "decoded_output",
        frames=len(frames),
        width=frames[0].width,
        height=frames[0].height,
        audio_streams=len(container.streams.audio),
    )
    frames[24].to_image().save(base / "export-frame.png")
with av.open(str(root / "output" / take["subfolder"] / take["file"])) as container:
    plain = list(container.decode(video=0))[24].to_ndarray(format="rgb24")
rendered = frames[24].to_ndarray(format="rgb24")
difference = np.abs(rendered.astype(float) - plain.astype(float))
record(
    "subtitle_pixels",
    bottom_mean_change=float(difference[-90:].mean()),
    upper_mean_change=float(difference[:120].mean()),
    note="pixel comparison; visual review separately",
)
archive = session.get(
    "http://127.0.0.1:8190/h3take/api/project/" + folder + "/archive?keep=all",
    timeout=180,
)
archive.raise_for_status()
(base / "test-project.zip").write_bytes(archive.content)
opened = req(
    "POST",
    "/projects/open",
    data=archive.content,
    headers={"Content-Type": "application/zip"},
)
restored = req("GET", "/project/" + opened["folder"])
record(
    "archive_restore",
    restored=opened["folder"],
    subtitle_equal=restored["subtitles"] == state["subtitles"],
    sound_count=len(restored["sounds"]),
    old_project_preserved=data()["revision"] == state["revision"],
    archive_sha256=hashlib.sha256(archive.content).hexdigest(),
)
# An actual ComfyUI prompt, independent of the separately failed H3 GPU attempt.
workflow = {
    "1": {
        "class_type": "EmptyImage",
        "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 255},
    },
    "2": {
        "class_type": "SaveImage",
        "inputs": {"images": ["1", 0], "filename_prefix": "H3_Acceptance/empty_image"},
    },
}
response = session.post(
    "http://127.0.0.1:8190/prompt",
    json={"prompt": workflow, "client_id": "h3-acceptance"},
    timeout=30,
)
response.raise_for_status()
pid = response.json()["prompt_id"]
for _ in range(30):
    history = session.get("http://127.0.0.1:8190/history/" + pid, timeout=10).json()
    if pid in history:
        break
    time.sleep(0.5)
entry = history.get(pid, {})
record(
    "actual_comfy_workflow",
    prompt_id=pid,
    status=entry.get("status"),
    outputs=entry.get("outputs"),
)
print(json.dumps(report, ensure_ascii=True))
