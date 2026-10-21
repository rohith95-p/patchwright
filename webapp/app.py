"""Patchwright web app.  Run:  python -m webapp.app   ->  http://127.0.0.1:8000

Endpoints
  GET  /api/config                 provider, models, price info
  GET  /api/examples               bundled benchmark bugs (one-click demos)
  POST /api/runs                   start a fix run (example or https repo + issue)
  GET  /api/runs                   run history
  GET  /api/runs/{id}              one run with all events
  GET  /api/runs/{id}/stream       server-sent events (live)
  GET  /api/bench                  aggregated benchmark results
  POST /api/bench/start            start a benchmark in the background
  GET  /api/bench/status           benchmark progress
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bench.run_bench import CONFIGS, build_repo
from bench.tasks import TASKS
from dashboard.server import results as bench_results
from patchwright.agent import run_agent
from patchwright.config import PRICES, PRICES_ARE_ESTIMATES, get_model, provider_name
from patchwright.llm import LLM
from patchwright.pr import proof_card
from webapp.monitor import monitor

ROOT = Path(__file__).resolve().parent.parent
WEB_RUNS = ROOT / "runs" / "web"
WEB_RUNS.mkdir(parents=True, exist_ok=True)
STATIC = Path(__file__).parent / "static"
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_RUNS", "2"))
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"  # public hosting: bundled bugs only, no custom repos, no benchmark control
MAX_QUEUE = int(os.getenv("MAX_QUEUED_RUNS", "6"))

app = FastAPI(title="Patchwright")
RUNS: dict[str, dict] = {}
LOCK = threading.Lock()
SLOTS = threading.Semaphore(MAX_CONCURRENT)
VAGUE = re.compile(r"\b(?:all|every|any|find)\b.{0,30}\bbugs?\b", re.I)  # "find all the bugs" is not a bug report
# a real bug report mentions code (brackets, quotes, numbers, identifiers) or says what should/does happen
CONCRETE = re.compile(r"""[()\[\]{}'"`=<>_0-9]|\b(?:should|expected?|returns?|raises?|throws?|errors?|exceptions?|crash\w*|wrong|instead|fails?|failing|incorrect|broken|when)\b""", re.I)
BENCH = {"running": False, "config": None, "started": None, "log": [], "proc": None}


class RunRequest(BaseModel):
    example_id: str | None = None
    repo_url: str | None = None
    issue: str | None = None
    mode: str = "tiered"


class BenchRequest(BaseModel):
    config: str = "tiered"
    workers: int = 2


def _save(run: dict) -> None:
    meta = {k: v for k, v in run.items() if k != "events"}
    (WEB_RUNS / f"{run['id']}.json").write_text(json.dumps({**meta, "events": run["events"]}), encoding="utf-8")


def _load_history() -> None:
    for f in sorted(WEB_RUNS.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            if r.get("status") == "running":
                r["status"] = "interrupted"
            RUNS[r["id"]] = r
        except (json.JSONDecodeError, KeyError):
            pass


def _worker(run: dict, repo: str, issue: str, tiers: list[str], example: dict | None) -> None:
    with SLOTS:
        run["status"] = "running"
        run["started"] = time.time()
        llm = LLM()

        def on_event(e: dict) -> None:
            run["events"].append(e)

        def hidden_check(sb) -> bool:
            for path, content in example["hidden"].items():
                sb.write(path, content)
            code, out = sb.exec("python -m pytest -q 2>&1 | tail -3")
            return "failed" not in out and "error" not in out.lower() and "passed" in out

        try:
            res = run_agent(llm, repo, issue, on_event=on_event, tiers=tiers, post_check=hidden_check if example else None)
            run["status"] = "solved" if res.solved else "unsolved"
            run["result"] = {"solved": res.solved, "tier": res.tier_reached, "steps": res.steps, "summary": res.summary,
                             "diff": res.diff, "seconds": round(res.seconds, 1), "hidden_ok": res.hidden_ok,
                             "cost_usd": round(llm.usage.cost_usd, 6),
                             "tokens": llm.usage.prompt_tokens + llm.usage.completion_tokens}
            if res.solved:
                models = {t: get_model(t) for t in ["nano", *tiers]}
                run["result"]["proof_card"] = proof_card(issue, res, llm.usage.by_tier, models)
        except BaseException as e:  # noqa: BLE001
            run["status"] = "error"
            run["error"] = f"{type(e).__name__}: {e}"[:500]
            run["traceback"] = traceback.format_exc()[-3000:]
        run["finished"] = time.time()
        run["events"].append({"type": "end", "status": run["status"]})
        _save(run)


@app.get("/api/config")
def config() -> dict:
    tiers = {}
    for t in ("nano", "super", "ultra"):
        try:
            tiers[t] = get_model(t)
        except SystemExit:
            tiers[t] = None
    return {"provider": provider_name(), "models": tiers, "prices": PRICES, "prices_are_estimates": PRICES_ARE_ESTIMATES,
            "max_concurrent": MAX_CONCURRENT, "demo_mode": DEMO_MODE}


@app.get("/api/examples")
def examples() -> list[dict]:
    return [{"id": t["id"], "difficulty": t["difficulty"], "issue": t["issue"],
             "files": list(t["files"].keys())} for t in TASKS]


@app.post("/api/runs")
def start_run(req: RunRequest) -> dict:
    if req.mode not in CONFIGS:
        raise HTTPException(400, f"mode must be one of {list(CONFIGS)}")
    example = None
    if req.example_id:
        example = next((t for t in TASKS if t["id"] == req.example_id), None)
        if not example:
            raise HTTPException(404, "unknown example")
        repo = build_repo(example, Path(tempfile.mkdtemp(prefix="pwweb-")))
        issue, label = example["issue"], f"example: {example['id']}"
    else:
        if DEMO_MODE:
            raise HTTPException(403, "Demo mode: only the bundled example bugs can be run on this server.")
        if not req.repo_url or not re.match(r"^https://[\w.\-]+/[\w.\-/]+$", req.repo_url):
            raise HTTPException(400, "repo_url must be an https git URL")
        if not req.issue or len(req.issue.strip()) < 10:
            raise HTTPException(400, "describe the bug (at least 10 characters)")
        if (VAGUE.search(req.issue) and len(req.issue) < 120) or not CONCRETE.search(req.issue) or len(req.issue.split()) < 6:
            raise HTTPException(400, "Patchwright fixes one specific bug at a time. Describe what goes wrong: what you did, what you expected, what happened instead. It does not search a repo for unknown bugs.")
        repo, issue, label = req.repo_url, req.issue.strip(), req.repo_url
    if sum(1 for r in RUNS.values() if r["status"] in ("queued", "running")) >= MAX_QUEUE:
        raise HTTPException(429, "The server is busy. Try again in a minute.")
    rid = uuid.uuid4().hex[:10]
    run = {"id": rid, "label": label, "issue": issue, "mode": req.mode, "status": "queued", "events": [],
           "created": time.time(), "example": bool(example)}
    with LOCK:
        RUNS[rid] = run
    threading.Thread(target=_worker, args=(run, repo, issue, CONFIGS[req.mode], example), daemon=True).start()
    return {"id": rid}


@app.get("/api/runs")
def list_runs() -> list[dict]:
    rows = sorted(RUNS.values(), key=lambda r: r["created"], reverse=True)
    return [{k: v for k, v in r.items() if k not in ("events",)} | {"n_events": len(r["events"])} for r in rows[:50]]


@app.get("/api/runs/{rid}")
def get_run(rid: str) -> dict:
    if rid not in RUNS:
        raise HTTPException(404, "no such run")
    return RUNS[rid]


@app.get("/api/runs/{rid}/stream")
async def stream(rid: str):
    if rid not in RUNS:
        raise HTTPException(404, "no such run")
    run = RUNS[rid]

    async def gen():
        i = 0
        while True:
            while i < len(run["events"]):
                yield f"data: {json.dumps(run['events'][i])}\n\n"
                i += 1
            if run["status"] not in ("queued", "running") and i >= len(run["events"]):
                yield "event: close\ndata: {}\n\n"
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.get("/api/bench")
def bench() -> dict:
    return {"results": bench_results(), "prices_are_estimates": PRICES_ARE_ESTIMATES,
            "tasks": [{"id": t["id"], "difficulty": t["difficulty"]} for t in TASKS]}


@app.post("/api/bench/start")
def bench_start(req: BenchRequest) -> dict:
    if DEMO_MODE:
        raise HTTPException(403, "Disabled in demo mode.")
    if req.config not in CONFIGS:
        raise HTTPException(400, "bad config")
    if BENCH["running"]:
        raise HTTPException(409, "a benchmark is already running")
    BENCH.update(running=True, config=req.config, started=time.time(), log=[])
    proc = subprocess.Popen([sys.executable, "-m", "bench.run_bench", "--config", req.config, "--workers", str(req.workers)],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    BENCH["proc"] = proc

    def pump() -> None:
        for line in proc.stdout:
            if not line.startswith("warning"):
                BENCH["log"].append(line.rstrip())
        proc.wait()
        BENCH["running"] = False

    threading.Thread(target=pump, daemon=True).start()
    return {"started": True}


@app.get("/api/monitor")
def monitor_api() -> dict:
    return monitor()


@app.get("/api/bench/status")
def bench_status() -> dict:
    return {k: BENCH[k] for k in ("running", "config", "started", "log")}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
_load_history()

if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), log_level="warning")
