Main benchmark (before multi-action turns; see Efficiency below). Compared on the same **40 runs** for every strategy (20 bugs, up to 2 repeats each). "Truly fixed" means the hidden test the agent never saw also passes.

| Strategy | Truly fixed | Avg time per bug | Tokens | Cost | Cost per fixed bug |
|---|---|---|---|---|---|
| Tiered (Super, then Ultra) | 34/40 (85%) | 124s | 2,371,016 | $2.02 | $0.059 |
| Routed (Nano triage decides) | 37/40 (92%) | 101s | 2,155,329 | $1.68 | $0.045 |
| Super only | 20/40 (50%) | 94s | 1,382,028 | $0.63 | $0.031 |
| Ultra only | 33/40 (82%) | 71s | 1,817,270 | $2.64 | $0.080 |

In the tiered runs, Super alone fixed 23 of the 34 truly fixed bugs; Ultra rescued the other 11.

Routing: Nano rated every benchmark bug easy or medium and refused none, so the routed strategy took exactly the same path as tiered. The gap between the two rows is run-to-run noise, not a routing benefit. (Refusing invalid tasks is tested separately, see README.)

Runs the harness accepted but the hidden test rejected: Tiered (Super, then Ultra) 1, Ultra only 1.

Cost sensitivity: per fixed bug, tiered uses 42,647 Super + 26,563 Ultra tokens, Ultra-only uses 54,529 Ultra tokens. Tiered is cheaper than Ultra-only only if Ultra costs more than 1.5x Super per token; at equal prices it would cost 27% more.

Average times are only loosely comparable: the strategies ran at the same time with different numbers of parallel jobs, so each was slowed by a different amount of shared load.

Dollar figures are tokens times the per-token prices in `patchwright/prices.json` (Super and Ultra read from the Nebius usage page; Nano's price is an estimate). The agent is not deterministic, so repeat the benchmark before reading small differences as real.
