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

To be filled in from real Nebius Token Factory runs (`results/` holds the raw files).

| Strategy | Truly fixed | Avg time per bug | Tokens | Est. cost per fixed bug |
|---|---|---|---|---|
| Tiered (Super, then Ultra) | | | | |
| Super only | | | | |
| Ultra only | | | | |

Dollar figures use per-token price estimates in `patchwright/prices.json`, not official Nebius billing.

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
