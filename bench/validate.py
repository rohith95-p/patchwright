"""Prove the benchmark is fair: on the buggy code visible tests pass and hidden tests fail;
on the reference fix (when provided) visible and hidden tests both pass.   python -m bench.validate"""
import subprocess
import sys
import tempfile
from pathlib import Path

from .tasks import TASKS


def pytest_ok(d: Path) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=d, capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    bad = 0
    for t in TASKS:
        d = Path(tempfile.mkdtemp())
        for p, c in t["files"].items():
            (d / p).write_text(c)
        vis = pytest_ok(d)
        for p, c in t["hidden"].items():
            (d / p).write_text(c)
        hid_buggy = pytest_ok(d)
        ref = None
        if "reference" in t:
            for p, c in t["reference"].items():
                (d / p).write_text(c)
            ref = pytest_ok(d)
        ok = vis and not hid_buggy and ref in (None, True)
        bad += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {t['id']:<22} visible_passes={vis} hidden_fails_on_bug={not hid_buggy} reference_passes={ref}")
    print(f"\n{len(TASKS) - bad}/{len(TASKS)} tasks valid")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
