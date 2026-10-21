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

    def __init__(self, script, tokens_per_call=100):
        self.script = list(script)
        self.usage = Usage()
        self.calls = 0
        self.tokens_per_call = tokens_per_call

    def chat(self, tier, messages, temperature=0.2, max_tokens=4096):
        if tier == "nano":
            return "hint: look at stats.py"
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
