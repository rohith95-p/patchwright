<p align="center"><img src="webapp/static/logo.svg" alt="Patchwright logo: a stitched patch holding a check mark that shades from amber to teal to violet" width="112"></p>

<h1 align="center">Patchwright</h1>

**A bug-fixing agent that only ships fixes it can prove.**

Built for the Nebius x NVIDIA Global AI Hackathon, Coding and Agentic Engineering track, on **NVIDIA Nemotron** models served by **Nebius Token Factory**, with web search by **Tavily**.

**Demo video:** https://youtu.be/eNE4-9LeZ0c (narration is synthesized speech)  
**Real pull request, CI green:** https://github.com/rohith95-p/cachetools-bugfix-demo/pull/1

> Benchmark numbers are filled in from real Nebius runs only. Until the table under [Results](#results) is filled in, there are no claims about accuracy or cost.

## The problem

AI coding assistants write a plausible fix and ask you to believe it. A wrong fix that looks right costs more than no fix, so every suggestion still needs a human to re-verify it from scratch.

## The idea

Patchwright treats the model as a proposer and a separate harness as the judge. A fix counts only when **code, not the model,** confirms all of this:

1. A new test the agent wrote **fails with the source change reverted**, so the test really captures the bug.
2. The same test **passes with the change restored**.
3. The project's **existing suite still passes**, and **no existing test file was edited**.
4. A short **edge-case review** (a second model call writes extra tests for the patch) finds no failing case.
5. **The project's own quality gates pass.** Patchwright reads the repository's config and CI files, finds the tools it uses (ruff, black, pyright, mypy, flake8), auto-fixes formatting and import order, and reports problems only on lines the patch changed. A fix that passes the tests but would fail the maintainers' CI is not finished. This came from a real failure: our first pull request passed its tests but failed 22 CI checks on lint and type rules; it is now green and the check exists so the next patch does not need a follow-up commit.

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

Tavily is called by the harness, not left to the model's initiative (in 300 recorded runs the model never asked for a search by itself). Every query and its sources are shown in the live run view, in the pull-request description under "Web sources consulted", and counted against a cap of 4 lookups per run (`TAVILY_MAX_PER_RUN`).

| When | What is searched | Where the result goes |
|---|---|---|
| **Real repository, before the model starts** | related public discussion: project name plus issue title, developer sources | the triage hint |
| **Real repository, before the model starts** | **official documentation** for the API in the issue title (Python docs, readthedocs, PyPI only, never the open web) | the agent's first message **and** the edge-case reviewer, so extra tests check documented behaviour |
| **Any run, on a library or version error** | `ModuleNotFoundError`, `ImportError`, missing attribute, unexpected keyword, `DeprecationWarning` | the next observation, immediately |
| **When the model asks** | its own query | the next observation |

Our own assertion errors and ordinary runtime errors are deliberately not searched.

**What we measured, honestly.** The 20 benchmark bugs are self-contained, so Tavily has nothing to add there. We tried running the documentation lookup on all of them anyway: it cost about 26% more tokens (1.02M vs 0.81M on the 15 bugs both runs finished) and fixed 15/15 versus 14/15, a difference inside run-to-run noise. The lookup returned standard-library pages that had nothing to do with the made-up bugs. So it is now **limited to real repositories**, where documentation exists, and the benchmark numbers contain no Tavily effect. On the one real task (cachetools) Tavily found related issues and the project's documentation, **not the fix**; the one run made after adding the documentation lookup did not solve the task, so we make no claim that it helps the fix rate. What it does give is grounding and an audit trail: the sources the agent saw are on the pull request.

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

- **Overview:** the pitch and the headline numbers, all computed from the result files; the three models and their real prices; the real pull request; and what we do *not* claim.
- **Fix a bug:** pick one of 20 bundled bugs or point at a public repo and describe one specific bug. Watch Nano, Super and Ultra work live, see each command, and watch the proof checks turn green. Copy a ready-made pull request description.
- **Results:** the real-world story run by run (including the four that failed and what each taught us), the before/after efficiency numbers, and the main benchmark with its per-bug matrix and patches.
- **How it works:** the pipeline as a diagram, with the proof gates and where Tavily fits.
- **Monitor** (local only): live view of benchmark runs in progress. **History:** every run with its full log.
- Vague requests such as "find all the bugs" are refused before any tokens are spent: Patchwright fixes a bug you describe, it does not hunt for unknown ones.
- The logo is a stitched patch holding a check mark whose stroke runs amber to teal to violet, the colours of Nano, Super and Ultra: three models, one proof.

## Real-world demo: a real bug in a real library, a real pull request

The benchmark above is synthetic, so we also ran Patchwright on a real, closed issue in a real open-source library:
[tkem/cachetools#423](https://github.com/tkem/cachetools/issues/423) (`RRCache.popitem()` on an empty cache leaks the `choice` function's exception instead of raising `KeyError`).

- **Target:** [rohith95-p/cachetools-bugfix-demo](https://github.com/rohith95-p/cachetools-bugfix-demo), a standalone copy (not a fork, so nothing touches upstream) of cachetools at the commit just before upstream fixed it. 337 existing tests.
- **Input:** only the text of the upstream issue (`examples/cachetools-423/issue.txt`).
- **Judge:** the upstream project's own regression test, which the agent never sees. It fails on the buggy code and passes with the real upstream fix (validated before any model call).
- **Result:** [pull request #1](https://github.com/rohith95-p/cachetools-bugfix-demo/pull/1). Nemotron 3 Super fixed it alone in 14 steps (about 3 minutes, about $0.055). The patch is two files: a 2-line emptiness check in `RRCache.popitem()` (the same approach upstream took) and a new test file. The upstream test passes with it.
- **Tavily on a real repository:** before the model starts, the harness searches developer sources for related discussion (project name plus issue title) and shows the sources in the live view and the pull request. It found related cachetools issues and the PyPI page, **not the fix**; the model still had to find the bug, write a failing test and make the change.

### The honest history: 7 runs, 2 solved

Each failure exposed a real flaw that the toy benchmark never showed, and each was fixed in Patchwright itself (with a regression test) before the next run. The raw runs are in `results/realworld/attempts/`.

| Run | Outcome | What it exposed, and the fix |
|---|---|---|
| 1 | not solved | a model closed its file block with `FILE>>}`; the file was lost three times. The parser now accepts a mistyped or missing closing marker |
| 2 | not solved | the model rewrote a 500-line source file from a 40-line excerpt and destroyed the module. Existing files are now changed with a targeted `edit`, and the sandbox refuses to overwrite a large file with a much shorter one |
| 3 | not solved | a garbled edit was read as an empty replacement and deleted a method. Edits use SEARCH/REPLACE blocks, and an edit without a readable replacement is refused, never applied |
| 4 | **solved**, but messy | the patch shipped five scratch files and Tavily returned a pest-control page. Scratch files are now excluded; Tavily searches developer sources with a clean query |
| 5 | not solved | the model put the edit markers inside JSON and then read whole files (328k tokens). Edits given as JSON fields are understood; reads are kept to small ranges and capped |
| 6 | **solved**, clean | the pull request above |
| 7 | not solved | re-run after adding the documentation lookup (below), Super only, 14 steps, $0.053. Same task, same code otherwise: a reminder that one run proves little. Kept in `results/realworld/attempts/`; the published result above is run 6 |

Total for all seven runs: about 1.4 million tokens and about $0.80. So on this real task Patchwright succeeds in some runs and not others: the result is 2 of 7, and the 5 failures are in the table, not hidden. Real repositories are harder than our toy bugs, and the loop-safety limits (token caps, repeat detection, refusing destructive edits) are what kept the four failures cheap.

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
- Public hosting: set `DEMO_MODE=1` to allow only the bundled bugs and disable custom repositories and benchmark control, and to cap spend: at most 40 runs per day (`DEMO_MAX_RUNS_PER_DAY`) and 4 per visitor per hour (`DEMO_MAX_RUNS_PER_IP_HOUR`). See [DEPLOY.md](DEPLOY.md).
- Known limit: the sandbox has network access so dependencies can be installed. Treat untrusted repositories accordingly.

## Layout

```
patchwright/   agent loop, sandbox (edit, scratch-file filter), context finder, Tavily tools, model client, doctor/configure, PR proof card
bench/         20-bug benchmark, runner with spend limits, fairness validator, efficiency and real-world runners
webapp/        FastAPI server and single-page UI
dashboard/     benchmark result aggregation
examples/      the real cachetools issue and the upstream test used as the judge
deploy/        one-script VM setup for the public demo
tests/         unit tests, free loop-protection tests (fake model), web API tests
```

## Feedback on the platforms

See [FEEDBACK.md](FEEDBACK.md).

License: MIT
