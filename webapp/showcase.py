"""Everything the landing and results pages show, computed from the committed result files in results/ (never typed in by hand)."""
import json
import time
from pathlib import Path

from bench.efficiency import load, stats as eff_stats
from bench.summary import summarise
from patchwright.config import PRICES

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
CASE = ROOT / "examples" / "cachetools-423"
_cache: dict = {"t": 0.0, "data": None}


def _bench() -> dict:
    out = {}
    for c in ("tiered", "routed", "super", "ultra"):
        f = RES / f"{c}-final.jsonl"
        if not f.exists():
            continue
        s = summarise(load(f))
        out[c] = {"runs": s["runs"], "fixed": s["fixed"], "rate": round(s["rate"], 1), "tokens": s["tokens"], "cost": round(s["cost"], 3),
                  "cost_per_fix": round(s["cost_per_fix"], 4) if s["cost_per_fix"] else None, "avg_s": round(s["avg_s"])}
    return out


def _efficiency() -> dict:
    out = {}
    for c in ("tiered", "ultra"):
        before, after = RES / f"{c}-final.jsonl", RES / "efficiency" / f"{c}-batching.jsonl"
        if before.exists() and after.exists():
            out[c] = {"before": eff_stats([r for r in load(before) if r.get("rep", 0) == 0]), "after": eff_stats(load(after))}
    return out


def _realworld() -> dict | None:
    base = RES / "realworld"
    if not (base / "cachetools-423.json").exists():
        return None
    meta = json.loads((CASE / "meta.json").read_text(encoding="utf-8"))
    lessons = json.loads((CASE / "lessons.json").read_text(encoding="utf-8"))
    attempts, final = [], None
    for f in sorted((base / "attempts").glob("*.json")) + [base / "cachetools-423.json"]:
        d = json.loads(f.read_text(encoding="utf-8"))
        info = lessons.get(f.name, {})
        attempts.append({"run": info.get("run"), "solved": d["solved"], "cost": d["cost_usd"], "tokens": d["tokens"], "calls": d["model_calls"],
                         "tier": d["tier"], "seconds": d["seconds"], "lesson": info.get("lesson", "")})
        if f.name == "cachetools-423.json":
            seen, sources = set(), []
            for s in d.get("sources", []):
                if s["url"] not in seen:
                    seen.add(s["url"])
                    sources.append({"title": s["title"], "url": s["url"]})
            final = {"solved": d["solved"], "upstream_test_passes": d.get("upstream_test_passes"), "tier": d["tier"], "seconds": d["seconds"],
                     "tokens": d["tokens"], "cost": d["cost_usd"], "summary": d["summary"], "sources": sources}
    attempts.sort(key=lambda a: a["run"] or 0)
    return {**meta, "attempts": attempts, "solved_runs": sum(1 for a in attempts if a["solved"]),
            "total_cost": round(sum(a["cost"] for a in attempts), 2), "final": final}


def showcase() -> dict:
    if _cache["data"] is not None and time.time() - _cache["t"] < 30:
        return _cache["data"]
    data = {"bench": _bench(), "efficiency": _efficiency(), "realworld": _realworld(),
            "prices": {k: list(v) for k, v in PRICES.items()}}
    _cache.update(t=time.time(), data=data)
    return data
