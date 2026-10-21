"""Free tests of the loop and spend protections: a scripted fake model replaces Nebius, so no tokens are spent.
Needs Docker (the sandbox is real); skipped without it."""
import json
import shutil
import subprocess

import pytest

from patchwright import agent
from patchwright.llm import Usage

HAS_DOCKER = shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
pytestmark = pytest.mark.skipif(not HAS_DOCKER, reason="Docker not available")


class FakeLLM:
    """Plays back scripted replies for the working tiers; answers triage with a fixed hint."""

    def __init__(self, script, tokens_per_call=100, nano_reply="hint: look at stats.py"):
        self.nano_reply = nano_reply
        self.script = list(script)
        self.usage = Usage()
        self.calls = 0
        self.tokens_per_call = tokens_per_call

    def chat(self, tier, messages, temperature=0.2, max_tokens=4096):
        if tier == "nano":
            return self.nano_reply
        self.calls += 1
        self.usage.add(tier, self.tokens_per_call, 10, 0.0)
        return self.script.pop(0) if self.script else json.dumps({"action": "run", "cmd": f"echo filler {self.calls}"})


def act(**kw):
    return json.dumps(kw)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("PROVIDER", "nebius")
    for t in ("NANO", "SUPER", "ULTRA"):
        monkeypatch.setenv(f"{t}_MODEL", f"fake-{t.lower()}")
    monkeypatch.setattr(agent, "EDGE_REVIEW", False)


@pytest.fixture
def repo(tmp_path):
    from bench.run_bench import build_repo
    from bench.tasks import TASKS
    task = next(t for t in TASKS if t["id"] == "avg-offbyone")
    return build_repo(task, tmp_path), task


def events_of(llm, repo_url, issue, tiers):
    evs = []
    res = agent.run_agent(llm, repo_url, issue, on_event=evs.append, tiers=tiers)
    return res, evs


def test_scripted_correct_fix_is_solved_and_proven(repo):
    url, task = repo
    llm = FakeLLM([
        act(action="write", path="test_new.py", content="from stats import average\n\ndef test_a():\n    assert average([2, 4, 6]) == 4\n"),
        act(action="write", path="stats.py", content="def average(nums):\n    return sum(nums) / len(nums)\n"),
        act(action="finish", repro_test_cmd="python -m pytest -q test_new.py", suite_cmd="python -m pytest -q", summary="off by one"),
    ])
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert res.solved and res.tier_reached == "super"
    assert any(e["type"] == "verify" and e["ok"] for e in evs)


def test_same_action_repeated_aborts_the_tier_quickly(repo):
    url, task = repo
    llm = FakeLLM([act(action="run", cmd="ls")] * 30)
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert not res.solved
    assert llm.calls <= agent.MAX_SAME_ACTION + 1, "must stop after a few repeats, not run all steps"
    assert any(e["type"] == "abort" and "repeated" in e["reason"] for e in evs)


def test_unparseable_replies_abort_the_tier(repo):
    url, task = repo
    llm = FakeLLM(["I think the bug is in stats.py, let me look."] * 30)
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert not res.solved
    assert llm.calls <= agent.MAX_BAD_CONSEC
    assert any(e["type"] == "abort" and "unparseable" in e["reason"] for e in evs)


def test_repeated_rejected_finish_aborts_the_tier(repo):
    url, task = repo
    bogus = act(action="finish", repro_test_cmd="python -m pytest -q", suite_cmd="python -m pytest -q", summary="x")
    llm = FakeLLM([bogus, act(action="run", cmd="echo a"), bogus, act(action="run", cmd="echo b"), bogus, act(action="run", cmd="echo c")])
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert not res.solved
    assert sum(1 for e in evs if e["type"] == "verify" and not e["ok"]) == agent.MAX_REJECTS
    assert any(e["type"] == "abort" for e in evs)


def test_token_budget_stops_the_run(repo, monkeypatch):
    url, task = repo
    monkeypatch.setattr(agent, "TOKEN_BUDGET", 500)
    llm = FakeLLM([act(action="run", cmd=f"echo {i}") for i in range(20)], tokens_per_call=300)
    res, evs = events_of(llm, url, task["issue"], ["super", "ultra"])
    assert not res.solved and "budget" in res.summary
    assert llm.calls <= 3, "must stop as soon as the budget is exceeded, and never reach the second tier"
    assert any(e["type"] == "budget" for e in evs)


def test_time_limit_stops_the_run(repo, monkeypatch):
    url, task = repo
    monkeypatch.setattr(agent, "MAX_SECONDS", -1)
    llm = FakeLLM([act(action="run", cmd="ls")] * 5)
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert not res.solved and "time limit" in res.summary and llm.calls == 0


def test_escalation_resets_the_repo_and_tries_the_next_tier(repo):
    url, task = repo
    # super writes junk then loops (aborts); ultra then solves from a clean repo
    super_part = [act(action="write", path="stats.py", content="BROKEN"), act(action="run", cmd="ls")] + [act(action="run", cmd="ls")] * 6
    ultra_part = [
        act(action="write", path="test_new.py", content="from stats import average\n\ndef test_a():\n    assert average([2, 4, 6]) == 4\n"),
        act(action="write", path="stats.py", content="def average(nums):\n    return sum(nums) / len(nums)\n"),
        act(action="finish", repro_test_cmd="python -m pytest -q test_new.py", suite_cmd="python -m pytest -q", summary="fixed"),
    ]
    llm = FakeLLM(super_part + ultra_part)
    res, evs = events_of(llm, url, task["issue"], ["super", "ultra"])
    assert res.solved and res.tier_reached == "ultra"
    assert "BROKEN" not in res.diff, "ultra must start from a reset repo, not super's broken edit"
    assert any(e["type"] == "escalate" for e in evs)


def test_compact_clips_old_messages_but_keeps_recent_ones():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "FIRST" * 100}]
    for i in range(12):
        msgs.append({"role": "assistant" if i % 2 == 0 else "user", "content": f"msg{i} " + "x" * 2000})
    out = agent._compact(msgs)
    assert len(out) == len(msgs)
    assert out[1]["content"] == msgs[1]["content"], "task message must stay intact"
    assert all(len(m["content"]) < 400 for m in out[2:-agent.KEEP_FULL])
    assert out[-1]["content"] == msgs[-1]["content"]
    before, after = sum(len(m["content"]) for m in msgs), sum(len(m["content"]) for m in out)
    assert after < before * 0.7, f"expected at least 30% fewer characters, got {after}/{before}"


def test_harness_finishes_for_a_model_that_fixed_the_bug_but_never_called_finish(repo):
    url, task = repo
    fix = [act(action="write", path="test_new.py", content="from stats import average\n\ndef test_a():\n    assert average([2, 4, 6]) == 4\n"),
           act(action="write", path="stats.py", content="def average(nums):\n    return sum(nums) / len(nums)\n")]
    llm = FakeLLM(fix + [act(action="run", cmd=f"echo polishing {i}") for i in range(30)])
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert res.solved, "a verified fix must count even if the model never said finish"
    assert any(e["type"] == "verify" and e["ok"] and e.get("auto") for e in evs)
    assert llm.calls == agent.STEPS["super"], "it should have used its steps, then been verified by the harness"


def test_auto_finish_cannot_pass_a_wrong_fix(repo):
    url, task = repo
    # a test that passes without any fix (does not capture the bug) and no source change: must NOT be accepted
    llm = FakeLLM([act(action="write", path="test_new.py", content="def test_trivial():\n    assert True\n")] +
                  [act(action="run", cmd=f"echo {i}") for i in range(30)])
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert not res.solved
    assert not any(e["type"] == "verify" and e["ok"] for e in evs)


def test_model_is_warned_when_only_a_few_steps_are_left(repo):
    url, task = repo
    seen = []
    llm = FakeLLM([act(action="run", cmd=f"echo {i}") for i in range(30)])
    orig = llm.chat

    def spy(tier, messages, **kw):
        seen.append(messages[-1]["content"])
        return orig(tier, messages, **kw)
    llm.chat = spy
    events_of(llm, url, task["issue"], ["super"])
    assert any("call finish NOW" in m for m in seen)


# ---- routing: triage decides validity and difficulty, and the harness acts on it ----
def triage_json(valid=True, difficulty="medium", reason="because", hint="look at stats.py"):
    return json.dumps({"valid": valid, "reason": reason, "difficulty": difficulty, "hint": hint})


SOLVE = [act(action="write", path="test_new.py", content="from stats import average\n\ndef test_a():\n    assert average([2, 4, 6]) == 4\n"),
         act(action="write", path="stats.py", content="def average(nums):\n    return sum(nums) / len(nums)\n"),
         act(action="finish", repro_test_cmd="python -m pytest -q test_new.py", suite_cmd="python -m pytest -q", summary="fixed")]


def routed(llm, url, issue, tiers=("super", "ultra")):
    evs = []
    res = agent.run_agent(llm, url, issue, on_event=evs.append, tiers=list(tiers), route=True)
    return res, evs


def test_invalid_task_is_refused_and_nothing_more_is_spent(repo):
    url, task = repo
    llm = FakeLLM(SOLVE, nano_reply=triage_json(valid=False, reason="asks to find all bugs"))
    res, evs = routed(llm, url, "find all the bugs")
    assert not res.solved and res.summary.startswith("rejected") and res.steps == 0
    assert llm.calls == 0, "no working-tier model call may happen for an invalid task"
    assert any(e["type"] == "route" and e["valid"] is False for e in evs)


def test_hard_task_goes_straight_to_ultra_and_super_is_never_used(repo):
    url, task = repo
    llm = FakeLLM(SOLVE, nano_reply=triage_json(difficulty="hard"))
    res, evs = routed(llm, url, task["issue"])
    assert res.solved and res.tier_reached == "ultra"
    assert "super" not in llm.usage.by_tier
    assert next(e for e in evs if e["type"] == "route")["tiers"] == ["ultra"]


def test_easy_task_starts_with_super(repo):
    url, task = repo
    llm = FakeLLM(SOLVE, nano_reply=triage_json(difficulty="easy"))
    res, evs = routed(llm, url, task["issue"])
    assert res.solved and res.tier_reached == "super" and "ultra" not in llm.usage.by_tier
    assert next(e for e in evs if e["type"] == "route")["tiers"] == ["super", "ultra"]


def test_unreadable_triage_falls_back_to_the_normal_tiered_path(repo):
    url, task = repo
    llm = FakeLLM(SOLVE, nano_reply="I am not sure what you mean, sorry.")
    res, evs = routed(llm, url, task["issue"])
    assert res.solved and res.tier_reached == "super", "garbage triage must never block or mis-route a task"


def test_tiered_mode_ignores_the_triage_verdict(repo):
    url, task = repo
    llm = FakeLLM(SOLVE, nano_reply=triage_json(valid=False, difficulty="hard"))
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert res.solved and not any(e["type"] == "route" for e in evs)


def test_parse_triage_variants():
    from patchwright.agent import _parse_triage
    t = _parse_triage('```json\n' + triage_json(difficulty="Hard", hint="x") + '\n```')
    assert t["difficulty"] == "hard" and t["valid"] is True and t["parsed"]
    assert _parse_triage(triage_json(valid=False))["valid"] is False
    assert _parse_triage(triage_json(difficulty="impossible"))["difficulty"] == "medium"
    assert _parse_triage('{"valid": "false-ish"}')["valid"] is True, "only a real false may refuse a task"
    assert _parse_triage("<think>{\"valid\": false}</think>" + triage_json())["valid"] is True
    bad = _parse_triage("no json")
    assert bad["valid"] and bad["difficulty"] == "medium" and not bad["parsed"]


# ---- batching: several actions per reply save whole model calls ----
def batch(*acts_):
    out = []
    for a_ in acts_:
        out.append(json.dumps({k: v for k, v in a_.items() if k != "content"}) + ("\n<<<FILE\n" + a_["content"] + "FILE>>>" if "content" in a_ else ""))
    return "\n".join(out)


def test_one_reply_with_test_fix_and_finish_solves_in_a_single_model_call(repo):
    url, task = repo
    llm = FakeLLM([batch(
        dict(action="write", path="test_new.py", content="from stats import average\n\ndef test_a():\n    assert average([2, 4, 6]) == 4\n"),
        dict(action="write", path="stats.py", content="def average(nums):\n    return sum(nums) / len(nums)\n"),
        dict(action="finish", repro_test_cmd="python -m pytest -q test_new.py", suite_cmd="python -m pytest -q", summary="x"))])
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert res.solved and llm.calls == 1 and res.steps == 1


def test_a_failing_action_skips_the_rest_of_the_reply_and_tells_the_model(repo):
    url, task = repo
    seen = []
    llm = FakeLLM([batch(dict(action="run", cmd="exit 3"), dict(action="write", path="should_not_exist.py", content="x = 1\n")),
                   json.dumps(dict(action="run", cmd="ls"))])
    orig = llm.chat

    def spy(tier, messages, **kw):
        seen.append(messages[-1]["content"])
        return orig(tier, messages, **kw)
    llm.chat = spy
    events_of(llm, url, task["issue"], ["super"])
    assert any("skipped" in m and "exit=3" in m for m in seen), "the model must be told its second action did not run"
    listing = [m for m in seen if "stats.py" in m]
    assert listing and not any("should_not_exist.py" in m for m in listing), "the skipped write must not have happened"


def test_batch_is_capped_at_max_actions(repo):
    url, task = repo
    llm = FakeLLM([batch(*[dict(action="run", cmd=f"echo marker{i}") for i in range(6)])])
    evs = []
    agent.run_agent(llm, url, task["issue"], on_event=evs.append, tiers=["super"])
    ran = [e["detail"] for e in evs if e["type"] == "step" and "marker" in (e.get("detail") or "")]
    assert ran == [f"echo marker{i}" for i in range(agent.MAX_ACTIONS)]


# ---- Tavily wired into the loop (search is faked; no network, no credits) ----
def fake_search(calls):
    def _s(query, max_results=3):
        calls.append(query)
        return [{"title": "PyYAML docs", "url": "https://pyyaml.org/", "content": "pip install pyyaml"}]
    return _s


def test_library_error_triggers_automatic_research_and_is_shown_to_the_model(repo, monkeypatch):
    url, task = repo
    calls, seen = [], []
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(agent, "search", fake_search(calls))
    llm = FakeLLM([act(action="run", cmd="echo \"ModuleNotFoundError: No module named 'yaml'\"; exit 1"), act(action="run", cmd="ls")])
    orig = llm.chat

    def spy(tier, messages, **kw):
        seen.append(messages[-1]["content"])
        return orig(tier, messages, **kw)
    llm.chat = spy
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert len(calls) == 1 and "yaml" in calls[0]
    ev = next(e for e in evs if e["type"] == "research")
    assert ev["kind"] == "error" and ev["sources"][0]["url"] == "https://pyyaml.org/"
    assert any("Web research via Tavily" in m and "pip install pyyaml" in m for m in seen)


def test_own_assertion_errors_cause_no_search_and_repeats_are_not_searched_twice(repo, monkeypatch):
    url, task = repo
    calls = []
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(agent, "search", fake_search(calls))
    err = "echo \"ModuleNotFoundError: No module named 'yaml'\""
    llm = FakeLLM([act(action="run", cmd="echo 'AssertionError: Expected 4 got 6'; exit 1"), act(action="run", cmd=err + "; echo a"),
                   act(action="run", cmd=err + "; echo b")])
    events_of(llm, url, task["issue"], ["super"])
    assert len(calls) == 1, "assertion errors are not researched, and the same query is only searched once"


def test_model_requested_search_is_recorded_as_a_source_and_shown_in_the_proof_card(repo, monkeypatch):
    from patchwright.pr import proof_card
    url, task = repo
    calls = []
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(agent, "search", fake_search(calls))
    llm = FakeLLM([act(action="search", query="python average of list")] + SOLVE)
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert res.solved and [e["kind"] for e in evs if e["type"] == "research"] == ["asked by the model"]
    card = proof_card(task["issue"], res, llm.usage.by_tier, {"super": "m"})
    assert "Web sources consulted (Tavily)" in card and "https://pyyaml.org/" in card


def test_no_research_without_a_tavily_key(repo, monkeypatch):
    url, task = repo
    calls = []
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(agent, "search", fake_search(calls))
    events_of(FakeLLM([act(action="run", cmd="echo \"ModuleNotFoundError: No module named 'yaml'\"")]), url, task["issue"], ["super"])
    assert calls == []


def test_research_budget_per_run_is_capped(repo, monkeypatch):
    url, task = repo
    calls = []
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(agent, "search", fake_search(calls))
    monkeypatch.setattr(agent, "MAX_RESEARCH", 2)
    llm = FakeLLM([act(action="run", cmd=f"echo \"ModuleNotFoundError: No module named 'm{i}'\"") for i in range(6)])
    events_of(llm, url, task["issue"], ["super"])
    assert len(calls) == 2


def test_tavily_sources_are_kept_on_unsolved_runs_too(repo, monkeypatch):
    url, task = repo
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(agent, "search", fake_search([]))
    llm = FakeLLM([act(action="search", query="python average of a list")] + [act(action="run", cmd=f"echo {i}") for i in range(30)])
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert not res.solved and any(s["url"] == "https://pyyaml.org/" for s in res.evidence)


def test_agent_fixes_a_bug_with_the_edit_action(repo):
    url, task = repo
    reply = ('{"action":"write","path":"test_new.py"}\n<<<FILE\nfrom stats import average\n\ndef test_a():\n    assert average([2, 4, 6]) == 4\nFILE>>>\n'
             '{"action":"edit","path":"stats.py"}\n<<<OLD\n(len(nums) - 1)\nOLD>>>\n<<<NEW\nlen(nums)\nNEW>>>\n'
             '{"action":"finish","repro_test_cmd":"python -m pytest -q test_new.py","suite_cmd":"python -m pytest -q","summary":"off by one"}')
    llm = FakeLLM([reply])
    res, evs = events_of(llm, url, task["issue"], ["super"])
    assert res.solved and llm.calls == 1
    assert "-    return sum(nums) / (len(nums) - 1)" in res.diff and "+    return sum(nums) / len(nums)" in res.diff


def test_a_failed_edit_is_reported_and_the_rest_of_the_reply_is_skipped(repo):
    url, task = repo
    seen = []
    reply = ('{"action":"edit","path":"stats.py"}\n<<<OLD\nTHIS TEXT IS NOT THERE\nOLD>>>\n<<<NEW\nx\nNEW>>>\n{"action":"run","cmd":"echo SHOULD_NOT_RUN"}')
    llm = FakeLLM([reply, json.dumps(dict(action="run", cmd="ls"))])
    orig = llm.chat

    def spy(tier, messages, **kw):
        seen.append(messages[-1]["content"])
        return orig(tier, messages, **kw)
    llm.chat = spy
    events_of(llm, url, task["issue"], ["super"])
    assert any("edit failed" in m and "not found" in m and "skipped" in m for m in seen)
    assert not any("SHOULD_NOT_RUN" in m and "exit=0" in m for m in seen)
