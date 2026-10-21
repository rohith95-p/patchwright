"""Aggregation of benchmark result files for the web app (no server of its own).

results()  summarises the newest runs/<config>-*.jsonl per config: fix rate, time, tokens, cost per fixed bug.
"""
import glob
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
RESULTS = ROOT / "results"


def _rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _newest_per_config() -> dict[str, str]:
    """Files in results/ (committed, final numbers) win over scratch files in runs/."""
    latest: dict[str, str] = {}
    for folder in (RUNS, RESULTS):
        for f in sorted(glob.glob(str(folder / "*-*.jsonl"))):
            name = os.path.basename(f)
            if not name.startswith("live"):
                latest[name.split("-")[0]] = f
    return latest


def results() -> dict:
    out = {}
    for cfg, f in _newest_per_config().items():
        rows = _rows(f)
        n = len(rows) or 1
        fixed = sum(1 for r in rows if r.get("hidden_ok"))
        cost = sum(r.get("cost_usd") or 0 for r in rows)
        out[cfg] = {
            "file": os.path.basename(f), "tasks": len(rows), "fixed": fixed,
            "fix_rate": round(100 * fixed / n, 1), "repeats": max((r.get("rep", 0) for r in rows), default=0) + 1,
            "avg_seconds": round(sum(r.get("seconds") or 0 for r in rows) / n, 1),
            "total_tokens": sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in rows),
            "total_cost_usd": round(cost, 4),
            "cost_per_fix_usd": round(cost / fixed, 4) if fixed else None,
            "rows": rows,
        }
    return out
