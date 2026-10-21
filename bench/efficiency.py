"""Before/after comparison of the multi-action change, on the SAME bugs and the SAME repeat.

  python -m bench.efficiency [--write]

Baseline = repeat 0 of results/<strategy>-final.jsonl. After = results/efficiency/<strategy>-batching.jsonl (one repeat).
`--write` puts the table between the EFFICIENCY markers in README.md. Cost is tokens times the prices in
patchwright/prices.json. Seconds are shown but NOT comparable: the baseline ran under heavier parallel load.
"""
import argparse
import json
from pathlib import Path

from patchwright.pricing import row_cost

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- EFFICIENCY:START -->", "<!-- EFFICIENCY:END -->"
NAMES = {"tiered": "Tiered (Super, then Ultra)", "ultra": "Ultra only"}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stats(rows: list[dict]) -> dict:
    n, fixed = len(rows), sum(1 for r in rows if r.get("hidden_ok"))
    tokens = sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in rows)
    cost = sum(row_cost(r) for r in rows)
    return {"runs": n, "fixed": fixed, "tok_fix": tokens / fixed if fixed else None, "calls": sum(r.get("calls") or 0 for r in rows) / n if n else 0,
            "cost_fix": cost / fixed if fixed else None, "cost": cost, "secs": sum(r.get("seconds") or 0 for r in rows) / n if n else 0}


def pct(before: float | None, after: float | None) -> str:
    return f"{100 * (after - before) / before:+.0f}%" if before and after is not None else "n/a"


def table(pairs: dict[str, tuple[dict, dict]]) -> str:
    out = ["Same 20 bugs, one repeat each. \"Before\" is repeat 0 of the main benchmark; \"after\" adds multi-action turns "
           "(up to 3 actions per model reply).", "",
           "| Strategy | | Truly fixed | Tokens per fixed bug | Model calls per run | Cost per fixed bug | Total cost (20 runs) |",
           "|---|---|---|---|---|---|---|"]
    for c, (b, a) in pairs.items():
        out.append(f"| {NAMES.get(c, c)} | before | {b['fixed']}/{b['runs']} | {b['tok_fix']:,.0f} | {b['calls']:.1f} | ${b['cost_fix']:.3f} | ${b['cost']:.2f} |")
        out.append(f"| | **after** | {a['fixed']}/{a['runs']} | **{a['tok_fix']:,.0f}** ({pct(b['tok_fix'], a['tok_fix'])}) | {a['calls']:.1f} ({pct(b['calls'], a['calls'])}) "
                   f"| **${a['cost_fix']:.3f}** ({pct(b['cost_fix'], a['cost_fix'])}) | ${a['cost']:.2f} ({pct(b['cost'], a['cost'])}) |")
    notes = ["Why it saves so much: the whole conversation is re-sent on every call, so fewer turns means fewer calls *and* a shorter history on each call.",
             "Caveats: one repeat of 20 bugs, so a difference of one or two fixed bugs is noise. Seconds per run are left out on purpose: the baseline "
             "ran under heavier parallel load than the after runs, so they are not comparable; calls and tokens do not depend on load."]
    if "tiered" in pairs and "ultra" in pairs:
        t, u = pairs["tiered"][1], pairs["ultra"][1]
        if t["cost_fix"] and u["cost_fix"]:
            rel = 100 * (1 - t["cost_fix"] / u["cost_fix"])
            notes.append(f"With multi-action turns, tiered costs ${t['cost_fix']:.3f} per fixed bug and Ultra-only ${u['cost_fix']:.3f}: "
                         f"tiered is {abs(rel):.0f}% {'cheaper' if rel > 0 else 'more expensive'}, but makes {t['calls']:.1f} model calls per run against {u['calls']:.1f}, "
                         "because Super's attempts add turns. Batching helped Ultra-only the most, so the money saved by routing is now small; "
                         "choose tiered to minimise cost and Ultra-only to minimise latency.")
    return "\n".join(out) + "\n\n" + "\n\n".join(notes) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    pairs = {}
    for c in ("tiered", "ultra"):
        base = [r for r in load(ROOT / "results" / f"{c}-final.jsonl") if r.get("rep", 0) == 0]
        pairs[c] = (stats(base), stats(load(ROOT / "results" / "efficiency" / f"{c}-batching.jsonl")))
    md = table(pairs)
    print(md)
    if a.write:
        readme = ROOT / "README.md"
        s = readme.read_text(encoding="utf-8")
        i, j = s.find(START), s.find(END)
        if i == -1 or j == -1:
            raise SystemExit(f"README.md needs the markers {START} and {END}")
        readme.write_text(s[: i + len(START)] + "\n" + md + s[j:], encoding="utf-8")
        print("README efficiency table updated")


if __name__ == "__main__":
    main()
