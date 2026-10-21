"""Patchwright-RW: real closed issues from real libraries, judged by the upstream project's own tests.

  python -m bench.rw_bench --plan                    list the cases and the spending limits, call no model
  python -m bench.rw_bench --only boltons-123 -n 1   run some cases
  python -m bench.rw_bench --max-cost 4.0            run all, stopping when the estimated cost hits the cap

A case is a folder under cases/ with case.json (repo, base commit, test files), issue.txt (the issue as filed) and
test.diff (the upstream test change, never shown to the agent). The agent gets a fresh single-commit copy of the
repository at the parent of the fixing commit, so the fix is not in its history. A run counts as truly fixed only if
the upstream tests pass with the agent's patch applied.
Writes runs/rw-<timestamp>.jsonl.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from patchwright.agent import run_agent
from patchwright.llm import LLM
from patchwright.sandbox import Sandbox

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "cases"
TIERS = {"tiered": ["super", "ultra"], "super": ["super"], "ultra": ["ultra"]}
_CACHE_LOCK = threading.Lock()


def sh(*a, cwd=None, check=True):
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(a[:3])} failed: {(r.stderr or r.stdout)[-400:]}")
    return r.stdout


def load_cases(only=None) -> list[dict]:
    out = []
    for d in sorted(CASES.glob("*/case.json")):
        c = json.loads(d.read_text(encoding="utf-8"))
        c["dir"] = d.parent
        if not only or c["id"] in only:
            out.append(c)
    return out


def pristine_copy(case: dict) -> str:
    """The repository at the parent of the fixing commit, as a fresh one-commit repo with no history to look the fix up in."""
    cache = Path(tempfile.gettempdir()) / "pw-rw-cache" / case["id"]
    work = Path(tempfile.mkdtemp(prefix="pw-rwsrc-"))
    with _CACHE_LOCK:  # several attempts at the same case share one cached clone
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            sh("git", "clone", "-q", f"https://github.com/{case['repo']}.git", str(cache))
        sh("git", "-C", str(cache), "checkout", "-q", "-f", case["base_sha"])
        shutil.copytree(cache, work / "repo", ignore=shutil.ignore_patterns(".git"))
    sh("git", "init", "-q", cwd=work / "repo")
    sh("git", "add", "-A", cwd=work / "repo")
    sh("git", "-c", "user.name=rw", "-c", "user.email=rw@example.com", "commit", "-qm", "base", cwd=work / "repo")
    return str(work / "repo")


def judge(case: dict):
    """post_check: apply the upstream test change (never shown to the agent) and run the upstream test files."""
    def check(sb: Sandbox) -> bool:
        repo = str(sb.host_dir / "repo")
        patch = sb.host_dir / "upstream_test.diff"
        patch.write_text((case["dir"] / "test.diff").read_text(encoding="utf-8"), encoding="utf-8", newline="")
        if subprocess.run(["git", "-C", repo, "apply", "--whitespace=nowarn", str(patch)], capture_output=True).returncode:
            return False
        files = " ".join(case["test_files"])
        out = sb.exec(f"python -m pytest -q -p no:cacheprovider {files} 2>&1 | tail -4", timeout=600)[1]
        return "failed" not in out and "error" not in out.lower() and "passed" in out
    return check


def run_case(case: dict, strategy: str) -> dict:
    issue = (case["dir"] / "issue.txt").read_text(encoding="utf-8")
    llm, trace = LLM(), []
    row = {"id": case["id"], "strategy": strategy, "repo": case["repo"]}

    def keep(e: dict) -> None:
        if e["type"] in ("start", "end"):
            return
        t = {"type": e["type"], "tier": e.get("tier"), "tok": e.get("usage", {}).get("tokens")}
        for k, n in (("action", 20), ("detail", 90), ("why", 260), ("reason", 120), ("obs", 400), ("kind", 20), ("query", 120), ("head", 240), ("finish", 20)):
            if e.get(k):
                t[k] = str(e[k])[:n]
        if "ok" in e:
            t["ok"] = e["ok"]
        trace.append(t)

    t0 = time.time()
    try:
        res = run_agent(llm, pristine_copy(case), issue, on_event=keep, source_url=f"https://github.com/{case['repo']}", tiers=TIERS[strategy], post_check=judge(case))
        row.update(solved=res.solved, hidden_ok=bool(res.hidden_ok), tier=res.tier_reached, steps=res.steps, diff=res.diff[:3000],
                   summary=res.summary, sources=len({s["url"] for s in res.evidence}))
    except Exception as e:  # noqa: BLE001
        row.update(solved=False, hidden_ok=False, error=f"{type(e).__name__}: {e}"[:300])
    u = llm.usage
    row.update(seconds=round(time.time() - t0, 1), calls=u.calls, prompt_tokens=u.prompt_tokens, completion_tokens=u.completion_tokens,
               cost_usd=round(u.cost_usd, 6), by_tier=u.by_tier, trace=trace)
    return row


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="tiered", choices=TIERS)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("-n", "--repeats", type=int, default=1)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--max-cost", type=float, default=2.0, help="stop when the estimated cost of this batch reaches this many dollars")
    ap.add_argument("--max-errors", type=int, default=3)
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args()
    cases = load_cases(a.only)
    jobs = [c for c in cases for _ in range(a.repeats)]
    print(f"strategy={a.strategy} cases={len(cases)} runs={len(jobs)} cap=${a.max_cost:.2f} (about $0.06-0.15 per run expected)")
    for c in cases:
        print(f"  {c['id']:28s} {c['repo']}  tests: {' '.join(c['test_files'])}")
    if a.plan:
        return
    out = ROOT / "runs" / f"rw-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    out.parent.mkdir(exist_ok=True)
    state = {"cost": 0.0, "errors": 0, "stop": ""}
    lock = threading.Lock()

    def one(c):
        if state["stop"]:
            return None
        r = run_case(c, a.strategy)
        with lock:
            state["cost"] += r["cost_usd"]
            state["errors"] = state["errors"] + 1 if r.get("error") else 0
            if state["cost"] >= a.max_cost:
                state["stop"] = f"cost cap ${a.max_cost:.2f} reached"
            if state["errors"] >= a.max_errors:
                state["stop"] = f"{a.max_errors} runs in a row crashed"
        return r

    done = 0
    with ThreadPoolExecutor(a.workers) as ex, out.open("w", encoding="utf-8") as f:
        for r in ex.map(one, jobs):
            if r is None:
                continue
            f.write(json.dumps(r) + "\n")
            f.flush()
            done += 1
            print(f"{r['id']:28s} solved={r['solved']} truly_fixed={r['hidden_ok']} tier={r.get('tier')} steps={r.get('steps')} "
                  f"t={r['seconds']:.0f}s cost=${r['cost_usd']:.3f} {r.get('error', '')}", flush=True)
    if state["stop"]:
        print("STOPPED EARLY:", state["stop"])
    print(f"{done} runs, estimated cost ${state['cost']:.3f} -> {out}")


if __name__ == "__main__":
    main()
