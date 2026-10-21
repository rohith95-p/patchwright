# Patchwright

**A bug-fixing agent that only ships fixes it can prove.**

Built for the Nebius x NVIDIA Global AI Hackathon, Coding and Agentic Engineering track, on **NVIDIA Nemotron** models served by **Nebius Token Factory**, with web search by **Tavily**.

> Benchmark numbers are filled in from real Nebius runs only. Until the table under [Results](#results) is filled in, there are no claims about accuracy or cost.

## The problem

AI coding assistants write a plausible fix and ask you to believe it. A wrong fix that looks right costs more than no fix, so every suggestion still needs a human to re-verify it from scratch.

## The idea

Patchwright treats the model as a proposer and a separate harness as the judge. A fix counts only when **code, not the model,** confirms all of this:

1. A new test the agent wrote **fails with the source change reverted**, so the test really captures the bug.
2. The same test **passes with the change restored**.
3. The project's **existing suite still passes**, and **no existing test file was edited**.
4. A short **edge-case review** (a second model call writes extra tests for the patch) finds no failing case.

The model cannot talk its way past these checks.

## How the Nemotron tiers are used

| Tier | Role | When it runs |
|---|---|---|
| **Nano** | Reads the issue and the repository snapshot, points at likely files and cause | Once per run, cheap |
| **Super** | Reproduces the bug with a failing test, writes the patch | First attempt |
| **Ultra** | Fresh attempt on a **reset** repository, with notes on what failed | Only if Super is stuck |

Three strategies run on the same bugs so routing is measured, not assumed: `tiered` (Super then Ultra), `super` only and `ultra` only.

## Quick start

Requirements: Python 3.10+, Docker running, a Nebius Token Factory API key, optionally a Tavily key.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows. macOS/Linux: source .venv/bin/activate
pip install -e .
cp .env.example .env              # then set PROVIDER=nebius and your keys
python -m patchwright configure   # auto-picks the Nano/Super/Ultra model IDs your key can use
python -m patchwright doctor      # checks Docker, key, endpoint, each model tier, Tavily
python -m webapp.app              # open http://127.0.0.1:8000
```

Windows shortcut: double-click `start_web.bat`.

### Command line

```bash
python -m patchwright fix https://github.com/owner/repo "average([2, 4, 6]) returns 6.0 but should be 4.0"
python -m bench.run_bench --config tiered --repeats 3 --plan   # print the plan and cost estimate, call no model
python -m bench.run_bench --config tiered --repeats 3          # run it (also: --config super, --config ultra)
python -m bench.validate                                       # prove every benchmark bug is fair
python -m pytest tests -q
```

## The web app

- **Fix a bug:** pick one of 20 bundled bugs or point at a public repo and describe one specific bug. Watch Nano, Super and Ultra work live, see each command, and watch the proof checks turn green. Copy a ready-made pull request description.
- **Benchmark:** fix rate, cost per fixed bug and time per strategy, plus a task-by-task matrix with the patches.
- **History:** every run is saved with its full activity log.
- Vague requests such as "find all the bugs" are refused before any tokens are spent: Patchwright fixes a bug you describe, it does not hunt for unknown ones.

## Spending safety

Credits are limited, so loops are blocked at several levels (all covered by tests that use a scripted fake model and spend nothing):

| Guard | Default |
|---|---|
| Tokens per run | 150,000 (`RUN_TOKEN_BUDGET`) |
| Seconds per run | 900 (`RUN_MAX_SECONDS`) |
| Same action repeated | stop the attempt after 4 |
| Unparseable replies in a row | stop after 3 |
| Rejected "finish" attempts | stop after 3 |
| Old tool output in the prompt | clipped (about 40% fewer characters on a synthetic long history; real savings not yet measured) |
| Whole benchmark | `--max-tokens` and `--max-cost`, default 2x the printed estimate, plus stop after 3 crashed runs in a row |
| `--plan` | prints the expected and worst-case cost before spending anything |

## Benchmark design

`bench/tasks.py` and `bench/tasks_hard.py` hold 20 small repositories, each with one realistic bug (off-by-one, mutable default, pagination, case handling, even-length median, shallow merge, slug cleanup, LRU recency, inclusive date ranges, quoted CSV fields, currency rounding, a bug shared by two modules, token-bucket refill, touching intervals, version comparison, operator precedence, text wrapping, remainder splitting, cache expiry boundary and cycle detection).

Each has visible tests that **pass on the buggy code** (so they do not reveal the bug), an issue written like a user report, and a **hidden test the agent never sees**. A fix is "truly fixed" only if the hidden test passes too. `python -m bench.validate` proves the set is fair: visible tests pass and hidden tests fail on the buggy code, and for the 8 hard bugs a reference fix passes both.

This is a synthetic set built for fast, repeatable comparison. It is **not SWE-bench**; do not read the results as SWE-bench scores. Model output varies from run to run, so the benchmark supports `--repeats` and reports averages.

## Results

Real runs on Nebius Token Factory (raw files in `results/`, rebuilt with `python -m bench.summary`).

<!-- RESULTS:START -->
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
<!-- RESULTS:END -->

### What these numbers do and do not show

- **Super alone is not enough** (about half the bugs); Ultra is far stronger; tiered Super-then-Ultra reaches Ultra's fix rate at a somewhat lower estimated cost. Tiered spends more tokens than Ultra-only, because Super's unsuccessful attempts still cost tokens; the saving comes from Super tokens being cheaper.
- **Run-to-run noise is about +/-3 of 40.** Tiered and routed took identical paths (see below) yet differed on 7 of the 40 runs. Differences of a few bugs between strategies are not evidence.
- **The router's difficulty routing did not work on this benchmark.** Nano rated every bug easy or medium, never hard, and its labels did not predict which bugs Super-only failed (it failed half of each group). A replay of the saved runs also shows that a perfect router could save only around 10-15% of the estimated cost, and that cutting Super's step budget would lose most of Super's successes (11 of its 20 successes came at the 14-step limit). We report this as a negative result rather than tune the prompt on the test bugs.
- **The validity gate did work in a small check** (`python -m bench.validity_check`): Nano refused 10 of 10 hand-written non-bug tasks ("find all the bugs", feature requests, questions) and accepted 4 of 5 real bug reports. The one wrongly refused real bug is why the web app offers "Run anyway" when a task is refused.
- **A passing self-written test is strong evidence, not a guarantee.** The hidden tests caught 2 fixes that the agent's own checks accepted (1 of 40 tiered runs, 1 of 40 Ultra-only runs).
- **Earlier runs had two harness bugs** (a model's own new test file wrongly rejected after a failed review, and a working fix not counted when the model never said "finish"). They were found from saved traces, fixed with regression tests, and every strategy was re-run from scratch. The discarded runs are not used anywhere in this table.


## Safety

- Every run executes in a throwaway Docker container with memory and CPU limits; files can only be written inside the repository.
- The agent never merges anything and never edits existing tests (the harness rejects it).
- Public hosting: set `DEMO_MODE=1` to allow only the bundled bugs and disable custom repositories and benchmark control. See [DEPLOY.md](DEPLOY.md).
- Known limit: the sandbox has network access so dependencies can be installed. Treat untrusted repositories accordingly.

## Layout

```
patchwright/   agent loop, sandbox, model client, doctor/configure, PR proof card
bench/         20-bug benchmark, runner with spend limits, fairness validator
webapp/        FastAPI server and single-page UI
dashboard/     benchmark result aggregation
tests/         unit tests, free loop-protection tests (fake model), web API tests
```

## Feedback on the platforms

See [FEEDBACK.md](FEEDBACK.md).

License: MIT
