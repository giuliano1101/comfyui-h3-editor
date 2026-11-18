import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
base = repo / ".acceptance"
os.environ["CUDA_VISIBLE_DEVICES"] = "1"
sys.path[:0] = [str(repo), str(repo / "tests"), str(base / "ComfyUI")]
tempfile.tempdir = str(base / "mapped-tmp")
Path(tempfile.tempdir).mkdir(exist_ok=True)
import torch  # noqa: E402 - isolated ComfyUI path and GPU selected above

torch.cuda.init()
import comfy_aimdo.control as control  # noqa: E402 - CUDA DLL must be loaded first

assert control.init("cuda")
assert control.init_devices([0])
try:
    import test_hyperflow

    suite = unittest.defaultTestLoader.loadTestsFromName(
        "PinTest.test_comfyui_s_mapped_load_gives_weights_that_cuda_cannot_pin",
        test_hyperflow,
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "runtime": "isolated ComfyUI modules; initialized comfy-aimdo CUDA; second RTX 3090",
        "tests": result.testsRun,
        "failed": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "passed": result.testsRun
        - len(result.failures)
        - len(result.errors)
        - len(result.skipped),
    }
    (base / "mapped-loading.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report))
finally:
    control.deinit()
