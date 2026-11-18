import http.cookiejar
import json
import time
import urllib.request
from urllib.parse import quote
from pathlib import Path
import psutil

import argparse
import sys

parser = argparse.ArgumentParser(
    description="Bounded local H3 GPU test; no cloud calls or downloads"
)
parser.add_argument("--run", action="store_true")
if not parser.parse_args().run:
    sys.exit("Review the hardware and license conditions, then pass --run explicitly.")
repo = Path(__file__).resolve().parents[2]
out = repo / ".acceptance" / ("gpu-repeat-" + time.strftime("%Y%m%d_%H%M%S") + ".json")
jar = http.cookiejar.CookieJar()
http = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar)
)


def call(path, body=None, method=None):
    req = urllib.request.Request(
        "http://127.0.0.1:8190/h3take/api" + quote(path, safe="/?=&"),
        json.dumps(body).encode() if body is not None else None,
        {"X-H3-Editor": "1", "Content-Type": "application/json"},
        method=method,
    )
    with http.open(req, timeout=60) as r:
        return json.load(r)


call("/session")
name = "H3_GPU_验收_" + time.strftime("%Y%m%d_%H%M%S")
made = call("/projects", {"name": name})
folder = made["folder"]
project = made["project"]
project.update(
    profile="h3",
    format="1:1",
    sizes={"draft": "256x256", "final": "small"},
    style="Realistic video, natural daylight",
    loras=[],
)
call("/project/" + folder, project, "PUT")
added = call(
    "/project/" + folder + "/ops",
    {
        "ops": [
            {
                "op": "add_clip",
                "fields": {
                    "name": "纸船单卡测试",
                    "seconds": 2,
                    "action": "A small red paper boat floats slowly across a still pond in soft daylight. Static camera. Natural water sounds. No speech.",
                },
            }
        ]
    },
)
project = call("/project/" + folder)
clip = project["clips"][0]["id"]
record = {
    "project": folder,
    "clip": clip,
    "profile": "h3",
    "size": [256, 256],
    "gpu": "second RTX 3090, 24 GB",
    "timeout_seconds": 900,
    "paid_api": False,
    "state": "preparing",
}


def save():
    out.write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding="utf-8")


record["state"] = "submitting"
save()
try:
    submitted = call(
        "/project/" + folder + "/render",
        {"clip": clip, "quality": "draft", "seed": 20261118},
    )
    record.update(state="waiting", job=submitted["job"])
    save()
    started = time.monotonic()
    record["minimum_available_ram_mb"] = psutil.virtual_memory().available // 2**20
    low = 0
    while time.monotonic() - started < 900:
        project = call("/project/" + folder)
        take = project["clips"][0].get("takes", [])
        record["minimum_available_ram_mb"] = min(
            record["minimum_available_ram_mb"],
            psutil.virtual_memory().available // 2**20,
        )
        record["elapsed_seconds"] = round(time.monotonic() - started, 1)
        if take:
            record.update(state="succeeded", take=take[-1])
            save()
            break
        if not project.get("pending"):
            record.update(
                state="failed",
                error=project["clips"][0].get("error", "render stopped without a take"),
            )
            save()
            break
        low = low + 1 if psutil.virtual_memory().available < 4 * 2**30 else 0
        if low >= 3:
            call("/project/" + folder + "/cancel", {"clip": clip})
            record["state"] = "cancelled_for_memory_pressure"
            save()
            break
        save()
        time.sleep(3)
    else:
        call("/project/" + folder + "/cancel", {"clip": clip})
        record["state"] = "cancelled_at_timeout"
        save()
except Exception as error:
    record.update(state="unknown_or_rejected", error_type=type(error).__name__)
    save()
print(json.dumps(record, ensure_ascii=True, indent=2))
