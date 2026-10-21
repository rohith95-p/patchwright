"""Turn raw benchmark files into the results table and write it into README.md and results/SUMMARY.md.

  python -m bench.summary --tiered runs/tiered-A.jsonl [--tiered-fill runs/tiered-B.jsonl] \
        --super runs/super-C.jsonl --ultra runs/ultra-D.jsonl [--write]

Strategies are compared ONLY on the (bug, repeat) runs that all three have, so a spending cap that cut one
strategy short cannot flatter or punish it. `--tiered-fill` holds the repeat-2 runs of some bugs that were run later;
its rows are relabelled as repeat 1. Dollar figures use the estimated prices in patchwright/prices.json.
"""
import argparse
import json
import shutil
from pathlib import Path

from patchwright.pricing import row_cost

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
NAMES = {"tiered": "Tiered (Super, then Ultra)", "routed": "Routed (Nano triage decides)", "super": "Super only", "ultra": "Ultra only"}


def load(path: str, relabel_rep: int | None = None) -> list[dict]:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if relabel_rep is not None:
        for r in rows:
            r["rep"] = relabel_rep
    return rows


def key(r: dict) -> tuple:
    return r["id"], r.get("rep", 0)


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    fixed = sum(1 for r in rows if r.get("hidden_ok"))
    tokens = sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in rows)
    cost = sum(row_cost(r) for r in rows)
    return {"runs": n, "fixed": fixed, "rate": 100 * fixed / n if n else 0.0,
            "avg_s": sum(r.get("seconds") or 0 for r in rows) / n if n else 0.0,
            "tokens": tokens, "cost": cost, "cost_per_fix": cost / fixed if fixed else None,
            "errors": sum(1 for r in rows if r.get("error")),
            "false_pass": sum(1 for r in rows if r.get("solved") and not r.get("hidden_ok")),
            "at_super": sum(1 for r in rows if r.get("hidden_ok") and r.get("tier") == "super"),
            "refused": sum(1 for r in rows if str(r.get("summary", "")).startswith("rejected")),
            "to_ultra": sum(1 for r in rows if any(e.get("type") == "route" and e.get("tiers") == "['ultra']" for e in r.get("trace", [])))}


def compare(data: dict[str, list[dict]]) -> tuple[dict, int]:
    common = set.intersection(*(set(map(key, rows)) for rows in data.values()))
    return {c: summarise([r for r in rows if key(r) in common]) for c, rows in data.items()}, len(common)


def breakeven(data: dict[str, list[dict]], common: set) -> str | None:
    """How much more per token Ultra must cost than Super for tiered to beat Ultra-only on cost per fixed bug."""
    if "tiered" not in data or "ultra" not in data:
        return None
    t = [r for r in data["tiered"] if key(r) in common]
    u = [r for r in data["ultra"] if key(r) in common]
    tok = lambda rows, tier: sum(r.get("by_tier", {}).get(tier, {}).get("tokens", 0) for r in rows)  # noqa: E731
    ft, fu = sum(1 for r in t if r.get("hidden_ok")), sum(1 for r in u if r.get("hidden_ok"))
    if not ft or not fu:
        return None
    a, b = tok(t, "super") / ft, tok(u, "ultra") / fu - tok(t, "ultra") / ft
    if b <= 0:
        return None
    return (f"Cost sensitivity: per fixed bug, tiered uses {tok(t, 'super') / ft:,.0f} Super + {tok(t, 'ultra') / ft:,.0f} Ultra tokens, "
            f"Ultra-only uses {tok(u, 'ultra') / fu:,.0f} Ultra tokens. Tiered is cheaper than Ultra-only only if Ultra costs more than "
            f"{a / b:.1f}x Super per token; at equal prices it would cost {100 * ((a + tok(t, 'ultra') / ft) / (tok(u, 'ultra') / fu) - 1):.0f}% more.")


def markdown(stats: dict, n_common: int, total_runs: dict, sens: str | None = None) -> str:
    out = [f"Compared on the same **{n_common} runs** for every strategy (20 bugs, up to 2 repeats each). "
           "\"Truly fixed\" means the hidden test the agent never saw also passes.", "",
           "| Strategy | Truly fixed | Avg time per bug | Tokens | Cost | Cost per fixed bug |",
           "|---|---|---|---|---|---|"]
    for c in ("tiered", "routed", "super", "ultra"):
        if c not in stats:
            continue
        s = stats[c]
        cpf = f"${s['cost_per_fix']:.3f}" if s["cost_per_fix"] is not None else "n/a"
        out.append(f"| {NAMES[c]} | {s['fixed']}/{s['runs']} ({s['rate']:.0f}%) | {s['avg_s']:.0f}s | {s['tokens']:,} | ${s['cost']:.2f} | {cpf} |")
    notes = []
    if "tiered" in stats:
        t = stats["tiered"]
        notes.append(f"In the tiered runs, Super alone fixed {t['at_super']} of the {t['fixed']} truly fixed bugs; "
                     f"Ultra rescued the other {t['fixed'] - t['at_super']}.")
    if "routed" in stats:
        rs = stats["routed"]
        if rs["refused"] == 0 and rs["to_ultra"] == 0:
            notes.append("Routing: Nano rated every benchmark bug easy or medium and refused none, so the routed strategy took exactly the "
                         "same path as tiered. The gap between the two rows is run-to-run noise, not a routing benefit. "
                         "(Refusing invalid tasks is tested separately, see README.)")
        else:
            notes.append(f"Routing: Nano refused {rs['refused']} task(s) as not specific bug reports (every benchmark task is a real bug, so any refusal is a mistake) "
                         f"and sent {rs['to_ultra']} straight to Ultra; the rest started with Super.")
    fp = {NAMES[c]: s["false_pass"] for c, s in stats.items() if s["false_pass"]}
    if fp:
        notes.append("Runs the harness accepted but the hidden test rejected: " + ", ".join(f"{k} {v}" for k, v in fp.items()) + ".")
    err = {NAMES[c]: s["errors"] for c, s in stats.items() if s["errors"]}
    if err:
        notes.append("Crashed runs: " + ", ".join(f"{k} {v}" for k, v in err.items()) + ".")
    extra = {NAMES[c]: v for c, v in total_runs.items() if v != n_common}
    if extra:
        notes.append("Runs available per strategy before aligning: " + ", ".join(f"{k} {v}" for k, v in total_runs.items()) + ".")
    if sens:
        notes.append(sens)
    notes.append("Average times are only loosely comparable: the strategies ran at the same time with different numbers of parallel jobs, "
                 "so each was slowed by a different amount of shared load.")
    notes.append("Dollar figures are tokens times the per-token prices in `patchwright/prices.json` (Super and Ultra read from the Nebius usage page; Nano's price is an estimate). "
                 "The agent is not deterministic, so repeat the benchmark before reading small differences as real.")
    return "\n".join(out) + "\n\n" + "\n\n".join(notes) + "\n"


def inject(readme: str, block: str) -> str:
    a, b = readme.find(START), readme.find(END)
    if a == -1 or b == -1:
        raise SystemExit(f"README.md needs the markers {START} and {END}")
    return readme[: a + len(START)] + "\n" + block + readme[b:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiered", required=True)
    ap.add_argument("--tiered-fill")
    ap.add_argument("--super", required=True)
    ap.add_argument("--ultra", required=True)
    ap.add_argument("--routed", help="optional: results of the routed strategy")
    ap.add_argument("--write", action="store_true", help="copy raw files to results/ and update README.md")
    a = ap.parse_args()
    tiered = load(a.tiered) + (load(a.tiered_fill, relabel_rep=1) if a.tiered_fill else [])
    data = {"tiered": tiered, "super": load(a.super), "ultra": load(a.ultra)}
    if a.routed:
        data["routed"] = load(a.routed)
    stats, n_common = compare(data)
    common = set.intersection(*(set(map(key, rows)) for rows in data.values()))
    md = markdown(stats, n_common, {c: len(r) for c, r in data.items()}, breakeven(data, common))
    print(md)
    if a.write:
        res = ROOT / "results"
        res.mkdir(exist_ok=True)
        for c, rows in data.items():
            (res / f"{c}-final.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        (res / "SUMMARY.md").write_text(md, encoding="utf-8")
        readme = ROOT / "README.md"
        readme.write_text(inject(readme.read_text(encoding="utf-8"), md), encoding="utf-8")
        print("wrote results/*.jsonl, results/SUMMARY.md and the README results table")
        shutil.rmtree(ROOT / "bench" / "__pycache__", ignore_errors=True)


if __name__ == "__main__":
    main()
