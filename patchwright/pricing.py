"""Cost of a benchmark row at the current prices.

Rows store tokens per tier but only a run-wide split between prompt and completion tokens, so each tier's tokens are
split in that same ratio. Recomputing from tokens (instead of using the stored cost) keeps old results consistent
whenever the prices are corrected.
"""
from .config import PRICES


def row_cost(row: dict) -> float:
    p, c = row.get("prompt_tokens") or 0, row.get("completion_tokens") or 0
    frac_in = p / (p + c) if (p + c) else 0.8
    total = 0.0
    for tier, t in (row.get("by_tier") or {}).items():
        pin, pout = PRICES.get(tier, (0.0, 0.0))
        total += (t.get("tokens") or 0) * (frac_in * pin + (1 - frac_in) * pout) / 1_000_000
    return total
