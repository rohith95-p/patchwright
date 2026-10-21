"""Check that each real-world case is fair: the upstream test FAILS on the base commit and PASSES with the upstream fix.

  python -m bench.rw_validate [case ids]        (no model calls, no cost)
A case that does not behave this way (needs a missing dependency, a different Python, flaky) is reported and must be dropped.
"""
import subprocess
import sys

from patchwright.sandbox import Sandbox

from .rw_bench import load_cases, pristine_copy


def tests_result(sb: Sandbox, files: list[str]) -> tuple[bool, str]:
    out = sb.exec(f"python -m pytest -q -p no:cacheprovider {' '.join(files)} 2>&1 | tail -6", timeout=600)[1]
    return ("failed" not in out and "error" not in out.lower() and "passed" in out), out.strip().splitlines()[-1] if out.strip() else ""


def apply(sb: Sandbox, diff_path) -> bool:
    p = sb.host_dir / "x.diff"
    p.write_text(diff_path.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    return subprocess.run(["git", "-C", str(sb.host_dir / "repo"), "apply", "--whitespace=nowarn", str(p)], capture_output=True).returncode == 0


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    bad = []
    for c in load_cases(sys.argv[1:] or None):
        sb = Sandbox(pristine_copy(c))
        try:
            sb.setup()
            if not apply(sb, c["dir"] / "test.diff"):
                print(c["id"], "FAIL: test.diff does not apply"); bad.append(c["id"]); continue
            before, b = tests_result(sb, c["test_files"])
            if not apply(sb, c["dir"] / "fix_src.diff"):
                print(c["id"], "FAIL: fix_src.diff does not apply"); bad.append(c["id"]); continue
            after, a = tests_result(sb, c["test_files"])
            ok = (not before) and after
            print(f"{c['id']:22s} {'OK  ' if ok else 'FAIL'} base: {b} | with upstream fix: {a}")
            if not ok:
                bad.append(c["id"])
        finally:
            sb.close()
    print("invalid cases:", bad or "none")


if __name__ == "__main__":
    main()
