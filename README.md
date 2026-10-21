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

## How this uses Nebius and NVIDIA

- **NVIDIA Nemotron 3, three sizes, each with a job:** Nano (30B) triages and judges whether a task is a real bug report, Super (120B) writes the failing test and the patch, Ultra (550B) takes over only when Super is stuck. Model IDs are discovered from the provider (`python -m patchwright configure`), not hard-coded.
- **Nebius Token Factory** serves all of them through its OpenAI-compatible API. Everything in the results table ran there: 4 strategies x 40 runs, plus the runs behind the findings below. Where it helped: the full benchmark ran with up to 22 sandboxes calling it in parallel with no crashed runs (our client retries transient rate limits, so we did not count them), and per-model usage and price lines made it possible to measure cost per fixed bug from real billing rather than guesses.
- **Efficiency is the point, and it is measured.** About 75% of all tokens are prompt tokens, because the conversation is re-sent on every call. So the levers are fewer calls per fix (several actions per reply, history clipping, early stops for loops) and sending the expensive model only where it is needed (tiered escalation). The repository ships the harness and the numbers for both.
- **Other Nebius services:** only Token Factory is used for inference. The demo can run on any Linux VM with Docker (see [DEPLOY.md](DEPLOY.md)); we have not used Serverless Endpoints or Jobs.
- **Built during the submission period:** the whole project was created for this hackathon, starting on 2026-10-20.

## How Tavily is used

Tavily is used two ways: the model can ask for a search, and the harness searches on its own where it clearly helps, so the agent benefits even if the model never asks.

- **Library and version errors:** when a command fails with a `ModuleNotFoundError`, `ImportError`, a missing module attribute, an unexpected keyword argument, or a `DeprecationWarning`, the harness searches Tavily for that error and shows the top results to the model. The agent's own assertion errors and ordinary runtime errors are deliberately not searched.
- **Real repositories:** when the task is a real remote repository, the harness looks for related public discussion of the reported symptom before the model starts.
- **Visible and bounded:** every query and its sources appear in the live run view and under "Web sources consulted" in the pull-request description. At most 4 lookups per run (`TAVILY_MAX_PER_RUN`).
- **Honest limit:** on our 20-bug benchmark Tavily is never triggered (the bugs are self-contained, with no library errors), so the benchmark numbers do not include any Tavily effect. In 300 earlier recorded runs the model never asked for a search by itself, which is why the harness now does it. Tavily's value shows on real repositories, see the real-world demo.

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
<!-- RESULTS:END -->

### Efficiency: fewer model calls per fix

The biggest lever turned out to be the number of model calls: the whole conversation is re-sent on every call, and about 75% of tokens are prompt tokens. Models often send several actions in one reply, and the harness used to run only the first. It now runs up to three, in order, and stops at the first failure.

<!-- EFFICIENCY:START -->
Same 20 bugs, one repeat each. "Before" is repeat 0 of the main benchmark; "after" adds multi-action turns (up to 3 actions per model reply).

| Strategy | | Truly fixed | Tokens per fixed bug | Model calls per run | Cost per fixed bug | Total cost (20 runs) |
|---|---|---|---|---|---|---|
| Tiered (Super, then Ultra) | before | 18/20 | 65,626 | 18.4 | $0.048 | $0.87 |
| | **after** | 19/20 | **40,207** (-39%) | 13.6 (-26%) | **$0.024** (-51%) | $0.45 (-48%) |
| Ultra only | before | 16/20 | 59,687 | 11.3 | $0.087 | $1.39 |
| | **after** | 19/20 | **20,370** (-66%) | 7.5 (-33%) | **$0.029** (-67%) | $0.55 (-60%) |

Why it saves so much: the whole conversation is re-sent on every call, so fewer turns means fewer calls *and* a shorter history on each call.

Caveats: one repeat of 20 bugs, so a difference of one or two fixed bugs is noise. Seconds per run are left out on purpose: the baseline ran under heavier parallel load than the after runs, so they are not comparable; calls and tokens do not depend on load.

With multi-action turns, tiered costs $0.024 per fixed bug and Ultra-only $0.029: tiered is 18% cheaper, but makes 13.6 model calls per run against 7.5, because Super's attempts add turns. Batching helped Ultra-only the most, so the money saved by routing is now small; choose tiered to minimise cost and Ultra-only to minimise latency.
<!-- EFFICIENCY:END -->

### What these numbers do and do not show

- **Super alone is not enough** (about half the bugs). **Tiered Super-then-Ultra matches Ultra-only's fix rate (34 vs 33 of 40, within noise) at about 26% lower cost per fixed bug** ($0.059 vs $0.080): Ultra costs about 3.3x Super per token, and tiered spends the expensive tokens only on the bugs Super cannot fix. Tiered uses *more* tokens than Ultra-only (Super's unsuccessful attempts still cost tokens), so the saving exists only because Ultra is priced higher; if the two models cost the same per token, Ultra-only would be cheaper (break-even: Ultra above ~1.5x Super).
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
