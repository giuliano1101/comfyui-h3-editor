import concurrent.futures
import io
import json
import tempfile
import time
import uuid
import wave
from pathlib import Path
import requests

repo = Path(__file__).resolve().parents[2]
base = repo / ".acceptance"
url = "http://127.0.0.1:8190/h3take/api"
session = requests.Session()
session.trust_env = False
session.headers["X-H3-Editor"] = "1"
session.get(url + "/session").raise_for_status()
report = {"source": "synthetic audio only; no microphone", "checks": []}


def check(name, **values):
    report["checks"].append({"name": name, **values})
    (base / "actual-speech-safety.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


audio = (base / "command-zh.wav").read_bytes()
headers = {"Content-Type": "audio/wav", "X-H3-ASR-Request": str(uuid.uuid4())}
response = session.post(
    url + "/speech/transcribe", data=audio, headers=headers, timeout=70
)
check(
    "consent_required", status=response.status_code, passed=response.status_code == 409
)
headers.update({"X-H3-Local-Audio-Consent": "1", "X-H3-ASR-Request": str(uuid.uuid4())})
response = session.post(
    url + "/speech/transcribe", data=audio, headers=headers, timeout=70
)
response.raise_for_status()
check(
    "actual_local_transcription",
    result=response.json(),
    expected="添加一个镜头，名称是纸船。",
    quality="manual correction required; not an accuracy pass",
)
scratch = Path(tempfile.gettempdir())
before = set(scratch.glob("h3-local-asr-*"))
request_id = str(uuid.uuid4())
memory = io.BytesIO()
with wave.open(memory, "wb") as out:
    out.setparams((1, 2, 16000, 0, "NONE", "none"))
    out.writeframes(b"\x01\x00" * (30 * 16000))


def long_request():
    other = requests.Session()
    other.trust_env = False
    other.cookies.update(session.cookies)
    other.headers["X-H3-Editor"] = "1"
    return other.post(
        url + "/speech/transcribe",
        data=memory.getvalue(),
        headers={
            "Content-Type": "audio/wav",
            "X-H3-Local-Audio-Consent": "1",
            "X-H3-ASR-Request": request_id,
        },
        timeout=70,
    )


with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    future = pool.submit(long_request)
    time.sleep(0.35)
    cancel = session.post(
        url + "/speech/cancel", json={"request": request_id}, timeout=10
    )
    cancel.raise_for_status()
    outcome = future.result(timeout=10)
    check(
        "actual_worker_cancel",
        cancel=cancel.json(),
        response_status=outcome.status_code,
        body=outcome.json(),
        temporary_removed=set(scratch.glob("h3-local-asr-*")) == before,
    )
folder = "H3_综合验收_20261118"
data = session.get(url + "/project/" + folder).json()
sound = data["sounds"][0]
path = base / "ComfyUI/output" / sound["subfolder"] / sound["file"]
hidden = path.with_suffix(".acceptance-hidden")
assert (
    path.is_file()
    and not hidden.exists()
    and path.resolve().is_relative_to((base / "ComfyUI/output").resolve())
)
path.rename(hidden)
try:
    health = session.post(
        url + "/bridge/call",
        json={"name": "project_health", "arguments": {"project": folder}},
        timeout=10,
    )
    response = session.post(url + "/project/" + folder + "/export", json={}, timeout=10)
    check(
        "missing_audio_export_blocked",
        status=response.status_code,
        health=health.json(),
        passed=response.status_code == 409,
    )
finally:
    hidden.rename(path)
print(json.dumps(report, ensure_ascii=True))
