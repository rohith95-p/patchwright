"""bench.summary: the results table must compare strategies fairly and fill the README markers. No Docker, no model."""
import json

import pytest

from bench import summary
from patchwright.pricing import row_cost


def row(i, rep, ok, tier="super", cost=0.01, tokens=1000, solved=None, secs=10, error=None):
    r = {"id": i, "rep": rep, "hidden_ok": ok, "solved": ok if solved is None else solved, "tier": tier,
         "cost_usd": cost, "prompt_tokens": tokens, "completion_tokens": 0, "seconds": secs,
         "by_tier": {tier: {"tokens": tokens}}}
    if error:
        r["error"] = error
    return r


def test_strategies_are_compared_only_on_runs_all_of_them_have():
    tiered = [row("a", 0, True), row("b", 0, True), row("c", 0, False)]
    sup = [row("a", 0, True), row("b", 0, False)]            # cut short before c
    ult = [row("a", 0, True), row("b", 0, True), row("c", 0, True), row("z", 0, True)]
    stats, n = summary.compare({"tiered": tiered, "super": sup, "ultra": ult})
    assert n == 2 and all(s["runs"] == 2 for s in stats.values())
    assert stats["tiered"]["fixed"] == 2 and stats["super"]["fixed"] == 1 and stats["ultra"]["fixed"] == 2


def test_summarise_cost_per_fix_false_pass_and_tier_split():
    rows = [row("a", 0, True, "super", cost=0.02), row("b", 0, True, "ultra", cost=0.06),
            row("c", 0, False, "ultra", cost=0.04, solved=True), row("d", 0, False, error="boom")]
    s = summary.summarise(rows)
    assert s["fixed"] == 2 and s["at_super"] == 1 and s["false_pass"] == 1 and s["errors"] == 1
    expected = sum(row_cost(r) for r in rows)      # cost is recomputed from tokens at the current prices
    assert expected > 0 and s["cost"] == pytest.approx(expected) and s["cost_per_fix"] == pytest.approx(expected / 2)


def test_no_fixes_gives_no_cost_per_fix_instead_of_dividing_by_zero():
    assert summary.summarise([row("a", 0, False)])["cost_per_fix"] is None
    assert summary.summarise([])["runs"] == 0


def test_fill_file_is_relabelled_as_repeat_one(tmp_path):
    f = tmp_path / "fill.jsonl"
    f.write_text(json.dumps(row("x", 0, True)) + "\n", encoding="utf-8")
    assert summary.load(str(f), relabel_rep=1)[0]["rep"] == 1


def test_markdown_contains_all_strategies_and_honest_notes():
    data = {"tiered": [row("a", 0, True, "ultra", solved=True), row("b", 0, False, solved=True)],
            "super": [row("a", 0, True), row("b", 0, False)], "ultra": [row("a", 0, True), row("b", 0, True)]}
    stats, n = summary.compare(data)
    md = summary.markdown(stats, n, {c: len(r) for c, r in data.items()})
    assert "Tiered (Super, then Ultra)" in md and "Super only" in md and "Ultra only" in md
    assert "1/2 (50%)" in md and "rejected" in md and "per-token prices" in md


def test_inject_replaces_only_between_markers_and_requires_them():
    readme = f"before\n{summary.START}\nold\n{summary.END}\nafter\n"
    out = summary.inject(readme, "NEW TABLE\n")
    assert "NEW TABLE" in out and "old" not in out and out.startswith("before") and out.endswith("after\n")
    with pytest.raises(SystemExit):
        summary.inject("no markers here", "x")


def test_row_cost_uses_real_per_tier_prices():
    from patchwright.config import PRICES
    r = {"prompt_tokens": 800, "completion_tokens": 200, "by_tier": {"ultra": {"tokens": 1_000_000}}}
    pin, pout = PRICES["ultra"]
    assert row_cost(r) == pytest.approx(0.8 * pin + 0.2 * pout)
    assert row_cost({}) == 0 and row_cost({"by_tier": {"mystery": {"tokens": 5}}, "prompt_tokens": 1}) == 0


def test_efficiency_comparison_math_and_wording():
    from bench import efficiency
    b = efficiency.stats([row("a", 0, True, tokens=1000), row("b", 0, False, tokens=1000)])
    a = efficiency.stats([row("a", 0, True, tokens=400), row("b", 0, True, tokens=400)])
    assert b["fixed"] == 1 and a["fixed"] == 2 and b["tok_fix"] == 2000 and a["tok_fix"] == 400
    assert efficiency.pct(2000, 400) == "-80%" and efficiency.pct(None, 5) == "n/a" and efficiency.pct(10, None) == "n/a"
    md = efficiency.table({"tiered": (b, a), "ultra": (b, a)})
    assert "-80%" in md and "Seconds per run are left out on purpose" in md and "Tiered (Super, then Ultra)" in md
