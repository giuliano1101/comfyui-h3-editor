import json
from pathlib import Path
import requests
import av

base = Path(__file__).resolve().parents[2] / ".acceptance"
folder = "H3_综合验收_20261118_2"
session = requests.Session()
session.trust_env = False
session.headers["X-H3-Editor"] = "1"
url = "http://127.0.0.1:8190/h3take/api"


def req(method, path, body=None):
    r = session.request(method, url + path, json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(str(r.status_code) + " " + r.text[:500])
    return r.json()


req("GET", "/session")
data = req("GET", "/project/" + folder)
if len(data["timeline"]) != 1:
    raise RuntimeError("composition fixture already changed; preserve it")


def ops(operations):
    state = req("GET", "/project/" + folder)
    return req(
        "POST",
        "/project/" + folder + "/ops",
        {"revision": state["revision"], "ops": operations},
    )


ops([{"op": "split_item", "item": data["timeline"][0]["id"], "frame": 48}])
data = req("GET", "/project/" + folder)
second = sorted(data["timeline"], key=lambda i: i["start"])[1]
ops(
    [
        {
            "op": "update_item",
            "item": second["id"],
            "fields": {"start": 42, "transition": {"type": "dissolve", "frames": 6}},
        }
    ]
)
result = req("POST", "/project/" + folder + "/export", {"name": "分割叠化字幕验收"})
path = base / "ComfyUI/output" / result["subfolder"] / result["filename"]
with av.open(str(path)) as container:
    frames = list(container.decode(video=0))
    decoded = {
        "frames": len(frames),
        "width": frames[0].width,
        "height": frames[0].height,
        "audio_streams": len(container.streams.audio),
    }
    frames[45].to_image().save(base / "composition-frame.png")
data = req("GET", "/project/" + folder)
before = data["revision"]
ops(
    [
        {
            "op": "add_effect",
            "fields": {"name": "未生成的验收特效", "start": 0, "frames": 24},
        }
    ]
)
health = req(
    "POST", "/bridge/call", {"name": "project_health", "arguments": {"project": folder}}
)
refused = session.post(url + "/project/" + folder + "/export", json={}, timeout=15)
latest = req("GET", "/project/" + folder)
ops([{"op": "remove_effect", "effect": latest["effects"][-1]["id"]}])
report = {
    "environment": "real isolated ComfyUI",
    "project": folder,
    "export": result,
    "decoded": decoded,
    "timeline": data["timeline"],
    "subtitles": len(data["subtitles"]),
    "missing_effect": {
        "health": health,
        "export_status": refused.status_code,
        "passed": refused.status_code == 409,
    },
    "original_project_unchanged": req("GET", "/project/H3_综合验收_20261118")[
        "revision"
    ]
    == 8,
}
(base / "actual-composition.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=True))
