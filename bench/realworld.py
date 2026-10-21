"""Real-world demo: fix a real, closed upstream issue in a real library, then check the result against the upstream test.

  python -m bench.realworld --repo https://github.com/<you>/cachetools-bugfix-demo.git [--strategy tiered|ultra|super]

The target repository is tkem/cachetools at the commit just before upstream fixed issue #423 (see examples/cachetools-423).
The agent sees only the issue text. The upstream test, which the agent never sees, is applied afterwards as the judge.
Writes results/realworld/cachetools-423.json and prints the pull-request description.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from patchwright.agent import run_agent
from patchwright.config import get_model
from patchwright.llm import LLM
from patchwright.pr import proof_card
from patchwright.pricing import row_cost

ROOT = Path(__file__).resolve().parent.parent
CASE = ROOT / "examples" / "cachetools-423"
TIERS = {"tiered": ["super", "ultra"], "super": ["super"], "ultra": ["ultra"]}


def upstream_check(sb) -> bool:
    """Apply the upstream test (never shown to the agent) and run it against the agent's patch."""
    repo = str(sb.host_dir / "repo")
    patch = sb.host_dir / "upstream_test.diff"
    patch.write_text((CASE / "upstream_test.diff").read_text(encoding="utf-8"), encoding="utf-8", newline="")
    applied = subprocess.run(["git", "-C", repo, "apply", "--whitespace=nowarn", str(patch)], capture_output=True, text=True)
    if applied.returncode:
        return False
    code, out = sb.exec("python -m pytest -q tests/test_rr.py 2>&1 | tail -3", timeout=300)
    return "failed" not in out and "passed" in out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # summaries contain characters a Windows console cannot print
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="URL or path of the repository at the pre-fix commit")
    ap.add_argument("--strategy", default="tiered", choices=TIERS)
    a = ap.parse_args()
    issue = (CASE / "issue.txt").read_text(encoding="utf-8")
    llm, events = LLM(), []
    t0 = time.time()
    res = run_agent(llm, a.repo, issue, on_event=events.append, tiers=TIERS[a.strategy], post_check=upstream_check)
    secs = time.time() - t0
    u = llm.usage
    row = {"by_tier": u.by_tier, "prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens}
    out = {"case": "tkem/cachetools#423", "strategy": a.strategy, "solved": res.solved, "upstream_test_passes": res.hidden_ok,
           "tier": res.tier_reached, "steps": res.steps, "seconds": round(secs, 1), "model_calls": u.calls,
           "tokens": u.prompt_tokens + u.completion_tokens, "cost_usd": round(row_cost(row), 4), "summary": res.summary,
           "diff": res.diff, "sources": res.evidence, "events": [{k: v for k, v in e.items() if k != "usage"} for e in events if e["type"] != "triage"]}
    dest = ROOT / "results" / "realworld"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "cachetools-423.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"solved={res.solved} upstream_test_passes={res.hidden_ok} tier={res.tier_reached} steps={res.steps} "
          f"calls={u.calls} tokens={out['tokens']:,} cost=${out['cost_usd']:.3f} time={secs:.0f}s")
    print(f"web sources consulted: {len({s['url'] for s in res.evidence})}")
    if res.solved:
        models = {t: get_model(t) for t in ["nano", *TIERS[a.strategy]]}
        card = proof_card(issue.splitlines()[0], res, u.by_tier, models)
        (dest / "cachetools-423-pr.md").write_text(card, encoding="utf-8")
        print("\n" + card)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
