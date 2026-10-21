"""Live view of background benchmarks: finished rows, totals and the bugs being worked on right now.

Finished rows come from runs/*.jsonl and runs/bench_*.log. In-flight bugs are read from the running sandbox
containers: each one is a clone of a benchmark repo whose path names the bug and the repeat.
"""
import glob
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from patchwright.pricing import row_cost

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
MAP_FILE = RUNS / "monitor_map.json"

TASKS = [
    {"key": "ultra", "label": "Ultra only", "log": "bench_ultra.log", "glob": "ultra-*.jsonl", "total": 40, "workers": 7},
    {"key": "tiered", "label": "Tiered (Super, then Ultra)", "log": "bench_tiered.log", "glob": "tiered-*.jsonl", "total": 40, "workers": 6},
    {"key": "super", "label": "Super only", "log": "bench_super.log", "glob": "super-*.jsonl", "total": 40, "workers": 5},
    {"key": "routed", "label": "Routed (Nano triage decides)", "log": "bench_routed.log", "glob": "routed-*.jsonl", "total": 40, "workers": 4},
]
URL_RE = re.compile(r"pwbench-([A-Za-z0-9_]+)[\\/]+r(\d+)[\\/]+([\w\-]+)")
SUMMARY_RE = re.compile(r"^\w+: \d+/\d+ truly fixed", re.M)


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def inflight() -> list[dict]:
    """Bugs currently running, from live sandbox containers."""
    out = []
    for cid in _run(["docker", "ps", "-q", "--filter", "name=patchwright-"]).split():
        info = _run(["docker", "inspect", "-f", "{{.State.StartedAt}}|{{range .Mounts}}{{.Source}}{{end}}", cid]).strip()
        if "|" not in info:
            continue
        started, src = info.split("|", 1)
        cfg = Path(src) / ".git" / "config"
        try:
            m = URL_RE.search(cfg.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not m:
            continue  # a website run, not a benchmark
        try:
            t = datetime.fromisoformat(started[:26].rstrip("Z") + "+00:00")
            age = max(0, time.time() - t.timestamp())
        except ValueError:
            age = 0
        out.append({"root": m.group(1), "rep": int(m.group(2)), "id": m.group(3), "seconds": round(age)})
    return out


def _root_ctime(root: str) -> float:
    for base in (Path(tempfile_dir()),):
        p = base / f"pwbench-{root}"
        if p.exists():
            return p.stat().st_ctime
    return 0.0


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


def _mapping(live: list[dict]) -> dict[str, str]:
    """Which benchmark process (strategy) owns each pwbench-* temp root. Cached so it survives finishing runs."""
    try:
        mp = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        mp = {}
    roots = {r["root"] for r in live} - set(mp)
    if roots:
        counts = {r: sum(1 for x in live if x["root"] == r) for r in roots}
        free = [t["key"] for t in TASKS if t["key"] not in mp.values()]
        ordered = sorted(roots, key=lambda r: (-counts[r], _root_ctime(r)))  # busiest root = most workers
        by_workers = [t["key"] for t in sorted((t for t in TASKS if t["key"] in free), key=lambda t: -t["workers"])]
        for r, k in zip(ordered, by_workers):
            mp[r] = k
        try:
            MAP_FILE.write_text(json.dumps(mp), encoding="utf-8")
        except OSError:
            pass
    return mp


def _rows_for(task: dict, log_path: Path) -> list[dict]:
    files = sorted(glob.glob(str(RUNS / task["glob"])), key=lambda f: Path(f).stat().st_mtime)
    born = log_path.stat().st_ctime if log_path.exists() else 0
    files = [f for f in files if Path(f).stat().st_ctime >= born - 5]  # only files made by this log's run
    if not files:
        return []
    rows = []
    for line in Path(files[-1]).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows.append({"id": r["id"], "rep": r.get("rep", 0), "fixed": bool(r.get("hidden_ok")), "tier": r.get("tier"),
                         "steps": r.get("steps"), "seconds": r.get("seconds"), "error": r.get("error"),
                         "tokens": (r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0), "cost": row_cost(r)})
    return rows


def monitor() -> dict:
    live = inflight()
    mp = _mapping(live)
    cards = []
    for t in TASKS:
        log = RUNS / t["log"]
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        rows = _rows_for(t, log)
        finished = bool(SUMMARY_RE.search(text))
        stopped = re.search(r"STOPPED EARLY: (.+)", text)
        mine = [x for x in live if mp.get(x["root"]) == t["key"]]
        status = "finished" if finished else ("running" if (mine or log.exists()) else "waiting")
        cards.append({
            "key": t["key"], "label": t["label"], "status": status, "total": t["total"], "workers": t["workers"],
            "done": len(rows), "fixed": sum(r["fixed"] for r in rows), "errors": sum(1 for r in rows if r["error"]),
            "tokens": sum(r["tokens"] for r in rows), "cost": round(sum(r["cost"] for r in rows), 3),
            "stopped": stopped.group(1) if stopped else None,
            "running_now": sorted(({"id": x["id"], "rep": x["rep"], "seconds": x["seconds"]} for x in mine), key=lambda x: -x["seconds"]),
            "recent": rows[-6:][::-1],
        })
    return {"time": time.time(), "containers": len(live), "cards": cards,
            "tokens": sum(c["tokens"] for c in cards), "cost": round(sum(c["cost"] for c in cards), 3)}
