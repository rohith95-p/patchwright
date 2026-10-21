"""Run Patchwright-Bench with hard spending limits.

  python -m bench.run_bench --config tiered --repeats 3 --plan      show the plan and estimated cost, call no model
  python -m bench.run_bench --config tiered --repeats 3 --workers 3 run it
  configs: tiered (super -> ultra), super (super only), ultra (ultra only)

Safety limits (the run stops and keeps partial results when any is hit):
  --max-tokens  total tokens across the whole benchmark   (default 2x expected)
  --max-cost    estimated USD across the whole benchmark  (default 2x expected, min $0.50)
  --max-errors  consecutive crashed runs                   (default 3)
Writes runs/<config>-<timestamp>.jsonl with one line per run.
"""
import argparse
import json
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from patchwright.agent import TOKEN_BUDGET, run_agent
from patchwright.config import PRICES, provider_name
from patchwright.llm import LLM

from .tasks import TASKS

CONFIGS = {"tiered": ["super", "ultra"], "super": ["super"], "ultra": ["ultra"]}
EST_TOKENS_PER_RUN = {"tiered": 35_000, "super": 30_000, "ultra": 45_000}  # from measured runs; rough


def build_repo(task: dict, root: Path) -> str:
    d = root / task["id"]
    d.mkdir(parents=True)
    for path, content in task["files"].items():
        (d / path).write_text(content, encoding="utf-8", newline="\n")
    for cmd in (["git", "init", "-q"], ["git", "add", "."],
                ["git", "-c", "user.email=b@b", "-c", "user.name=bench", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=d, check=True, capture_output=True)
    return str(d)


def run_task(task: dict, config: str, root: Path, rep: int = 0) -> dict:
    llm = LLM()
    repo = build_repo(task, root / f"r{rep}")

    def hidden_check(sb) -> bool:
        for path, content in task["hidden"].items():
            sb.write(path, content)
        code, out = sb.exec("python -m pytest -q 2>&1 | tail -3")
        return "failed" not in out and "error" not in out.lower() and "passed" in out

    row = {"id": task["id"], "difficulty": task["difficulty"], "config": config, "rep": rep}
    trace: list[dict] = []

    def keep(e: dict) -> None:
        """Compact per-event trace so a failed run can be diagnosed afterwards without re-running it."""
        if e["type"] in ("start", "end"):
            return
        t = {"t": e.get("t"), "type": e["type"], "tier": e.get("tier"), "tok": e.get("usage", {}).get("tokens")}
        for k, n in (("action", 20), ("detail", 90), ("why", 220), ("reason", 120), ("obs", 110)):
            if e.get(k):
                t[k] = str(e[k])[:n]
        if "ok" in e:
            t["ok"] = e["ok"]
        trace.append(t)

    try:
        res = run_agent(llm, repo, task["issue"], on_event=keep, tiers=CONFIGS[config], post_check=hidden_check)
        row.update(solved=res.solved, hidden_ok=bool(res.hidden_ok), tier=res.tier_reached, steps=res.steps,
                   seconds=round(res.seconds, 1), diff=res.diff[:1500], summary=res.summary)
    except Exception as e:  # noqa: BLE001
        row.update(solved=False, hidden_ok=False, error=f"{type(e).__name__}: {e}"[:300])
    row["trace"] = trace
    u = llm.usage
    row.update(calls=u.calls, prompt_tokens=u.prompt_tokens, completion_tokens=u.completion_tokens,
               cost_usd=round(u.cost_usd, 6), by_tier=u.by_tier)
    return row


def estimate(config: str, runs: int) -> tuple[int, float]:
    tokens = EST_TOKENS_PER_RUN[config] * runs
    tier = "ultra" if config == "ultra" else "super"
    pin, pout = PRICES[tier]
    return tokens, tokens * (0.87 * pin + 0.13 * pout) / 1_000_000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="tiered", choices=CONFIGS)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--only", nargs="*", help="task ids")
    ap.add_argument("--repeats", type=int, default=1, help="runs per bug; results are averaged")
    ap.add_argument("--max-tokens", type=int, default=None, help="default: 2x the expected tokens")
    ap.add_argument("--max-cost", type=float, default=None, help="default: 2x the expected cost, at least $0.50")
    ap.add_argument("--max-errors", type=int, default=3)
    ap.add_argument("--plan", action="store_true", help="print the plan and estimated cost, then exit (no model calls)")
    a = ap.parse_args()
    tasks = [t for t in TASKS if not a.only or t["id"] in a.only]
    jobs = [(t, r) for r in range(a.repeats) for t in tasks]
    est_tok, est_usd = estimate(a.config, len(jobs))
    a.max_tokens = a.max_tokens or 2 * est_tok
    a.max_cost = a.max_cost or max(0.5, 2 * est_usd)
    worst_tok = TOKEN_BUDGET * len(jobs)
    print(f"provider={provider_name()} config={a.config} bugs={len(tasks)} repeats={a.repeats} runs={len(jobs)}")
    print(f"expected: ~{est_tok:,} tokens, ~${est_usd:.2f} (estimated prices)   per-run cap: {TOKEN_BUDGET:,} tokens")
    print(f"worst case if every run hit its cap: {worst_tok:,} tokens; this benchmark stops at "
          f"{a.max_tokens:,} tokens or ${a.max_cost:.2f}, whichever comes first")
    if a.plan:
        return

    out = Path("runs") / f"{a.config}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    out.parent.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="pwbench-"))
    lock = threading.Lock()
    state = {"tokens": 0, "cost": 0.0, "errors": 0, "stop": ""}
    ok = done = 0

    def guarded(job) -> dict | None:
        t, rep = job
        if state["stop"]:
            return None  # skipped: a limit was hit
        row = run_task(t, a.config, root, rep)
        with lock:
            state["tokens"] += row.get("prompt_tokens", 0) + row.get("completion_tokens", 0)
            state["cost"] += row.get("cost_usd", 0.0)
            state["errors"] = state["errors"] + 1 if row.get("error") else 0
            if state["tokens"] >= a.max_tokens:
                state["stop"] = f"token limit {a.max_tokens:,} reached"
            elif state["cost"] >= a.max_cost:
                state["stop"] = f"cost limit ${a.max_cost:.2f} reached"
            elif state["errors"] >= a.max_errors:
                state["stop"] = f"{state['errors']} runs in a row crashed (last: {row.get('error')})"
        return row

    with ThreadPoolExecutor(a.workers) as ex, out.open("w", encoding="utf-8") as f:
        for row in ex.map(guarded, jobs):
            if row is None:
                continue
            f.write(json.dumps(row) + "\n")
            f.flush()
            done += 1
            ok += row["hidden_ok"]
            print(f"{row['id']:<22} rep={row['rep']} solved={row['solved']} hidden_ok={row['hidden_ok']} tier={row.get('tier')} "
                  f"steps={row.get('steps')} t={row.get('seconds')}s tokens={row.get('prompt_tokens', 0) + row.get('completion_tokens', 0):,}"
                  f"{' ERROR ' + row['error'] if row.get('error') else ''}")
    if state["stop"]:
        print(f"\nSTOPPED EARLY: {state['stop']}. Partial results kept.")
    print(f"\n{a.config}: {ok}/{done} truly fixed ({done} of {len(jobs)} planned runs finished) "
          f"| tokens {state['tokens']:,} | est. cost ${state['cost']:.3f} -> {out}")


if __name__ == "__main__":
    main()
