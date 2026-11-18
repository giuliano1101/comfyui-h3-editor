"""Repeatable Windows test entry point, including portable Python's isolated path.

Example: python_embeded\python.exe tools\run_tests.py --dependencies .test-deps
All temporary media stay under the checkout. No ComfyUI server/model is started.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dependencies", help="optional isolated dependency directory")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--report", help="write non-secret machine-readable counts")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    if args.dependencies:
        sys.path.insert(0, str(Path(args.dependencies).resolve()))
    temporary = root / ".test-tmp"
    temporary.mkdir(exist_ok=True)
    tempfile.tempdir = str(temporary)
    os.environ["TMP"] = os.environ["TEMP"] = str(temporary)
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    counts = {"total": result.testsRun, "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
              "failed": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
              "skip_reasons": sorted({reason for _, reason in result.skipped}), "python": sys.version.split()[0]}
    print(json.dumps(counts))
    if args.report:
        Path(args.report).write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
