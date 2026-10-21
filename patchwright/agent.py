"""The Patchwright loop: reproduce -> failing test -> patch -> verify, with tiered model escalation.

The harness, not the model, decides success: a fix counts only if the reproduction test fails with
the source change reverted and passes with it, the existing suite passes, and no existing test file
was edited.
"""
import json
import os
import re
import time
from dataclasses import dataclass, field

from .config import get_model
from .llm import LLM
from .sandbox import Sandbox
from .tools import web_search

TIER_ORDER = ["super", "ultra"]  # nano only triages
STEPS = {"super": 14, "ultra": 18, "nano": 10}
TOKEN_BUDGET = int(os.getenv("RUN_TOKEN_BUDGET", "150000"))  # hard stop per run, protects your credits
EDGE_REVIEW = os.getenv("EDGE_REVIEW", "1") == "1"
MAX_SECONDS = int(os.getenv("RUN_MAX_SECONDS", "900"))  # wall-clock cap per run
MAX_BAD_CONSEC = 3   # consecutive unparseable replies before giving up on a tier
MAX_REJECTS = 3      # rejected "finish" attempts before giving up on a tier
MAX_SAME_ACTION = 4  # identical action repeated this many times means the model is stuck
KEEP_FULL = 6        # most recent messages kept verbatim; older ones are clipped to save tokens

REVIEW_PROMPT = """A fix was made for this bug report.

Issue:
{issue}

Patch:
{diff}

Write a short pytest file that probes edge cases this patch might still get wrong. Use ONLY behaviour the issue states or that is obviously correct for the function (empty input, single element, boundaries, repeated or adjacent separators, input already in the target form, order independence). Do not invent new requirements. Reply with ONLY the Python source of the test file, no explanation and no markdown fences. Import from the modules shown in the patch."""

SYSTEM = """You are Patchwright, an expert software engineer fixing a bug in a repository mounted at /work in a Linux sandbox (Python 3.11, bash, pytest installed; there is no git inside the sandbox).

You are given a snapshot of the repository below, so you usually do not need to list or cat files. Work like this:
1. Understand the issue and find the root cause in the code.
2. Write a NEW test file (never edit existing test files) that reproduces the bug, and run it to see it fail.
3. Fix the SOURCE code with a minimal, correct change that addresses the root cause, not just the example in the issue.
4. Run your new test and the whole existing suite; both must pass.
5. Finish.

Reply with exactly ONE JSON object per turn and nothing else. Actions:
{"action":"run","cmd":"<bash command>"}
{"action":"write","path":"<relative path>"}
<<<FILE
<the full new content of the file, written raw: no JSON escaping, no quotes around it>
FILE>>>
(For "write", the JSON line contains only the action and path. The file content goes between <<<FILE and FILE>>> exactly as it should appear on disk.)
{"action":"search","query":"<web search; only for unfamiliar library or error behaviour>"}
{"action":"finish","repro_test_cmd":"<command that runs ONLY your new test, e.g. python -m pytest -q test_bug.py>","suite_cmd":"<command that runs the full suite, e.g. python -m pytest -q>","summary":"<root cause and fix in 2-4 sentences>"}

Your new test file must have several assertions, not only the example from the issue: the issue's example, the boundary cases around it (empty input, single element, the smallest and largest valid values, repeated or adjacent separators, values already in the target form), and the general rule the issue implies. Before you finish, re-read your fix and ask what input would still break it; add that input to your test.

Rules: install dependencies with pip if needed. Never delete tests. Never modify existing test files. Think about edge cases (empty input, boundaries, types) the issue implies. The harness will independently revert your source change to confirm your test fails without it, so your test must truly capture the bug. Be efficient; you have a limited number of steps."""


@dataclass
class Result:
    solved: bool
    tier_reached: str
    steps: int
    summary: str = ""
    diff: str = ""
    evidence: list = field(default_factory=list)
    seconds: float = 0.0
    hidden_ok: bool | None = None
    bad_replies: int = 0


FILE_BLOCK = re.compile(r"<<<FILE[ \t]*\r?\n(.*?)\r?\n?[ \t]*FILE>>>", re.S)


def _parse_action(text: str) -> dict | None:
    """First JSON action in a reply. For `write`, the content comes from a raw <<<FILE ... FILE>>> block
    (no escaping, so models cannot break it); inline JSON "content" is still accepted."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    dec = json.JSONDecoder(strict=False)  # tolerate raw newlines inside strings
    for m in re.finditer(r"\{", text):
        try:
            obj, end = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "action" in obj:
            if obj["action"] == "write" and not obj.get("content"):
                block = FILE_BLOCK.search(text[m.start() + end:])
                if block:
                    obj["content"] = block.group(1).rstrip("\n") + "\n"
            return obj
    if '"write"' in text and '\\\\"' in text:  # models sometimes double-escape quotes inside JSON strings
        return _parse_action(text.replace('\\\\"', '\\"')) if text.replace('\\\\"', '\\"') != text else None
    return None


def _looks_like_broken_write(text: str) -> bool:
    return bool(re.search(r'"action"\s*:\s*"write"', text))


def triage(llm: LLM, snapshot: str, issue: str) -> str:
    msg = (f"Issue:\n{issue}\n\nRepository snapshot:\n{snapshot[:9000]}\n\n"
           "In under 120 words: which files and functions are most likely involved, and what is the suspected root cause?")
    try:
        return llm.chat("nano", [{"role": "user", "content": msg}], max_tokens=2500)
    except Exception as e:  # noqa: BLE001
        return f"(triage unavailable: {e})"


def _verify(sb: Sandbox, act: dict) -> tuple[bool, str]:
    repro, suite = act.get("repro_test_cmd", ""), act.get("suite_cmd", "")
    if not repro or not suite:
        return False, "finish needs both repro_test_cmd and suite_cmd."
    ok, why = sb.prove_fails_without_fix(repro)
    if not ok:
        return False, why
    code, out = sb.exec(repro)
    if code != 0:
        return False, f"Repro test still fails:\n{out}"
    code, out = sb.exec(suite, timeout=600)
    if code != 0:
        return False, f"Existing suite fails:\n{out}"
    return True, "verified"


def _edge_review(llm: LLM, tier: str, sb: Sandbox, issue: str) -> tuple[bool, str]:
    """One cheap extra call: a reviewer writes edge-case tests for the patch; the harness runs them."""
    try:
        code = llm.chat(tier, [{"role": "user", "content": REVIEW_PROMPT.format(issue=issue, diff=sb.diff()[:6000])}],
                        temperature=0.1, max_tokens=2500)
    except Exception as e:  # noqa: BLE001
        return True, f"review skipped: {e}"
    code = re.sub(r"<think>.*?</think>", "", code, flags=re.S).strip()
    code = re.sub(r"^```(?:python)?\s*|\s*```$", "", code).strip()
    if "def test" not in code:
        return True, "review produced no tests"
    sb.write("test_edge_review_tmp.py", code)
    try:
        rc, out = sb.exec("python -m pytest -q -x -p no:cacheprovider test_edge_review_tmp.py 2>&1 | tail -25", timeout=120)
        failed = "failed" in out.lower() or "error" in out.lower()
        return (not failed), out[-1100:]
    finally:
        sb.exec("rm -f test_edge_review_tmp.py")


def _compact(msgs: list[dict]) -> list[dict]:
    """Keep the system prompt, the first user message and the last KEEP_FULL messages verbatim; clip the middle.
    The whole history is re-sent on every call, so clipping old tool output is the biggest token saver."""
    if len(msgs) <= KEEP_FULL + 2:
        return msgs
    out = msgs[:2]
    for m in msgs[2:-KEEP_FULL]:
        c = m["content"]
        out.append({"role": m["role"], "content": c if len(c) <= 350 else c[:300] + " ...[clipped]"})
    return out + msgs[-KEEP_FULL:]


def _detail(act: dict) -> str:
    a = act.get("action")
    if a == "run":
        return act.get("cmd", "")
    if a == "write":
        return f"{act.get('path', '')}\n{str(act.get('content', ''))[:700]}"
    if a == "search":
        return act.get("query", "")
    return ""


def run_agent(llm: LLM, repo_url: str, issue: str, ref: str | None = None, on_event=lambda e: None,
              tiers=None, post_check=None) -> Result:
    t0 = time.time()
    tiers = tiers or TIER_ORDER

    def emit(ev: dict) -> None:
        u = llm.usage
        ev["t"] = round(time.time() - t0, 1)
        ev["usage"] = {"calls": u.calls, "tokens": u.prompt_tokens + u.completion_tokens,
                       "cost_usd": round(u.cost_usd, 6), "by_tier": u.by_tier}
        on_event(ev)

    emit({"type": "start", "repo": repo_url, "tiers": tiers, "models": {t: get_model(t) for t in ["nano", *tiers]}})
    with Sandbox(repo_url, ref) as sb:
        sb.setup()
        snapshot = sb.snapshot()
        hint = triage(llm, snapshot, issue)
        emit({"type": "triage", "tier": "nano", "text": hint})
        notes = ""
        steps = 0
        bad = 0
        reviewed = False
        for ti, tier in enumerate(tiers):
            if ti > 0:
                sb.reset()
            emit({"type": "tier_start", "tier": tier, "model": get_model(tier)})
            first = (f"Issue:\n{issue}\n\nRepository snapshot:\n{snapshot}\n\n"
                     f"Triage hint from a fast model (may be wrong):\n{hint}{notes}")
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": first}]
            recent: list[str] = []
            bad_run = rejects = 0
            abort = ""
            for _ in range(STEPS.get(tier, 14)):
                u = llm.usage
                if u.prompt_tokens + u.completion_tokens > TOKEN_BUDGET:
                    emit({"type": "budget", "tier": tier})
                    return Result(False, tier, steps, "stopped: token budget reached", sb.diff(), [], time.time() - t0, None, bad)
                if time.time() - t0 > MAX_SECONDS:
                    emit({"type": "budget", "tier": tier, "reason": "time limit"})
                    return Result(False, tier, steps, "stopped: time limit reached", sb.diff(), [], time.time() - t0, None, bad)
                steps += 1
                reply = llm.chat(tier, _compact(msgs))
                msgs.append({"role": "assistant", "content": reply})
                act = _parse_action(reply)
                if not act:
                    bad += 1
                    bad_run += 1
                    emit({"type": "bad_reply", "tier": tier, "head": reply[:200]})
                    if bad_run >= MAX_BAD_CONSEC:
                        abort = f"{bad_run} unparseable replies in a row"
                        break
                    hint = ("Your write action could not be parsed. Send the JSON line with only action and path, then the file "
                            "content raw between <<<FILE and FILE>>>.") if _looks_like_broken_write(reply) else "Reply with one valid JSON action object."
                    msgs.append({"role": "user", "content": hint})
                    continue
                bad_run = 0
                a = act["action"]
                sig = json.dumps(act, sort_keys=True)[:300]
                if recent.count(sig) + 1 >= MAX_SAME_ACTION:
                    abort = f"the same action was repeated {MAX_SAME_ACTION} times"
                    break
                loop_warn = "\n(You repeated an action you already tried; change your approach.)" if recent.count(sig) >= 1 else ""
                recent.append(sig)
                if a == "run":
                    code, out = sb.exec(str(act.get("cmd") or "true"))
                    obs = f"exit={code}\n{out}"
                elif a == "write":
                    try:
                        if not act.get("path") or act.get("content") is None:
                            raise ValueError("a write needs a path and the file content between <<<FILE and FILE>>> on the lines after the JSON")
                        sb.write(act["path"], act["content"])
                        obs = f"wrote {act['path']}"
                    except Exception as e:  # noqa: BLE001
                        obs = f"write failed: {e}"
                elif a == "search":
                    obs = web_search(act.get("query", ""))
                elif a == "finish":
                    ok, why = _verify(sb, act)
                    emit({"type": "verify", "tier": tier, "ok": ok, "why": why[:1200]})
                    if ok and EDGE_REVIEW and not reviewed:
                        reviewed = True
                        rok, rmsg = _edge_review(llm, tier, sb, issue)
                        emit({"type": "review", "tier": tier, "ok": rok, "why": rmsg[:1200]})
                        if not rok:
                            ok, why = False, ("An independent edge-case review of your fix found a failing case:\n" + rmsg +
                                              "\nDecide whether your fix is incomplete. If so, fix the source code and finish again. "
                                              "If the review test itself contradicts the issue, finish again unchanged.")
                    if ok:
                        res = Result(True, tier, steps, act.get("summary", ""), sb.diff(), [], time.time() - t0, None, bad)
                        res.hidden_ok = post_check(sb) if post_check else None
                        emit({"type": "done", "solved": True, "tier": tier, "steps": steps, "summary": res.summary,
                              "diff": res.diff, "repro_cmd": act.get("repro_test_cmd"), "suite_cmd": act.get("suite_cmd")})
                        return res
                    obs = why
                    rejects += 1
                    if rejects >= MAX_REJECTS:
                        abort = f"{rejects} rejected finish attempts"
                        emit({"type": "step", "tier": tier, "action": a, "detail": "", "obs": obs[:1500]})
                        break
                else:
                    obs = f"unknown action {a!r}"
                emit({"type": "step", "tier": tier, "action": a, "detail": _detail(act), "obs": obs[:1500]})
                msgs.append({"role": "user", "content": obs + loop_warn})
            if abort:
                emit({"type": "abort", "tier": tier, "reason": abort})
            tried = "\n".join(m["content"][:300] for m in msgs[-5:] if m["role"] == "user")
            notes += (f"\n\nA previous attempt with tier '{tier}' did not succeed ({abort or 'ran out of steps'}). "
                      f"Its last observations were:\n{tried}\nThe repository has been reset to its original state. "
                      "Take a fresh, careful look at the root cause.")
            if ti + 1 < len(tiers):
                emit({"type": "escalate", "from": tier, "to": tiers[ti + 1]})
        res = Result(False, tiers[-1], steps, "unsolved", sb.diff(), [], time.time() - t0, None, bad)
        emit({"type": "done", "solved": False, "tier": tiers[-1], "steps": steps, "summary": "unsolved", "diff": res.diff})
        return res
