Compared on the same **40 runs** for every strategy (20 bugs, up to 2 repeats each). "Truly fixed" means the hidden test the agent never saw also passes.

| Strategy | Truly fixed | Avg time per bug | Tokens | Est. cost | Est. cost per fixed bug |
|---|---|---|---|---|---|
| Tiered (Super, then Ultra) | 34/40 (85%) | 124s | 2,371,016 | $1.47 | $0.043 |
| Routed (Nano triage decides) | 37/40 (92%) | 101s | 2,155,329 | $1.24 | $0.034 |
| Super only | 20/40 (50%) | 94s | 1,382,028 | $0.62 | $0.031 |
| Ultra only | 33/40 (82%) | 71s | 1,817,270 | $1.61 | $0.049 |

In the tiered runs, Super alone fixed 23 of the 34 truly fixed bugs; Ultra rescued the other 11.

Routing: Nano rated every benchmark bug easy or medium and refused none, so the routed strategy took exactly the same path as tiered. The gap between the two rows is run-to-run noise, not a routing benefit. (Refusing invalid tasks is tested separately, see README.)

Runs the harness accepted but the hidden test rejected: Tiered (Super, then Ultra) 1, Ultra only 1.

Average times are only loosely comparable: the strategies ran at the same time with different numbers of parallel jobs, so each was slowed by a different amount of shared load.

Dollar figures use the estimated prices in `patchwright/prices.json`, not official Nebius billing. The agent is not deterministic, so repeat the benchmark before reading small differences as real.
