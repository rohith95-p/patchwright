"""The Patchwright loop: reproduce -> failing test -> patch -> verify, with tiered model escalation.

The harness, not the model, decides success: a fix counts only if the reproduction test fails with
the source change reverted and passes with it, the existing suite passes, and no existing test file
was edited.
"""
import json
import os
import re
import shlex
import time
from dataclasses import dataclass, field

from .config import get_model
from .context import own_modules, relevant_excerpts
from .llm import LLM
from .sandbox import Sandbox
from .tools import format_results, research_query, search

TIER_ORDER = ["super", "ultra"]  # nano only triages
STEPS = {"super": 14, "ultra": 18, "nano": 10}
TOKEN_BUDGET = int(os.getenv("RUN_TOKEN_BUDGET", "150000"))  # hard stop per run, protects your credits
EDGE_REVIEW = os.getenv("EDGE_REVIEW", "1") == "1"
MAX_SECONDS = int(os.getenv("RUN_MAX_SECONDS", "900"))  # wall-clock cap per run
MAX_BAD_CONSEC = 3   # consecutive unparseable replies before giving up on a tier
MAX_REJECTS = 3      # rejected "finish" attempts before giving up on a tier
MAX_SAME_ACTION = 4  # identical action repeated this many times means the model is stuck
TOKEN_BUDGET_REMOTE = int(os.getenv("RUN_TOKEN_BUDGET_REMOTE", "300000"))  # real repositories have bigger files
MAX_RESEARCH = int(os.getenv("TAVILY_MAX_PER_RUN", "4"))  # Tavily lookups per run (errors, related discussion, model requests)
REMOTE_RESEARCH = os.getenv("TAVILY_CONTEXT", "1") == "1"  # look up related discussion for real remote repositories
MAX_FEEDBACK = 12000  # characters of results returned to the model per reply (real repositories have huge files)
MAX_ACTIONS = 3      # actions executed per model reply; each extra action saves a whole model call
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

To change an EXISTING file use edit, which replaces only the lines that must change:
{"action":"edit","path":"<existing file>"}
<<<<<<< SEARCH
<the exact current text to replace, copied from the file, including indentation>
=======
<the replacement text>
>>>>>>> REPLACE
The SEARCH text must match the file exactly and be unique in it (add a surrounding line if it is not). You may put several SEARCH/REPLACE blocks after one edit line; they are applied in order. NEVER rewrite an existing source file with write: you usually have seen only part of a large file, and a rewrite destroys the rest. Use write only for NEW files.

To save steps you may put up to 3 actions in one reply, one after another; they run in order and you get every result back together. Batch what does not depend on a result you need to read first, for example: write your new test and run it; or write the fix and run the new test and the suite. If an action fails, the rest of the reply is skipped. Do not batch past something whose output you must see before deciding.
{"action":"search","query":"<web search; only for unfamiliar library or error behaviour>"}
{"action":"finish","repro_test_cmd":"<command that runs ONLY your new test, e.g. python -m pytest -q test_bug.py>","suite_cmd":"<command that runs the full suite, e.g. python -m pytest -q>","summary":"<root cause and fix in 2-4 sentences>"}

Your new test file must have several assertions, not only the example from the issue: the issue's example, the boundary cases around it (empty input, single element, the smallest and largest valid values, repeated or adjacent separators, values already in the target form), and the general rule the issue implies. Before you finish, re-read your fix and ask what input would still break it; add that input to your test.

Read files in small ranges, never whole large files: use grep -n, or sed -n 'START,ENDp' file (about 60 lines at a time). The code most relevant to the issue is already given to you above, so start from it.

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


FILE_BLOCK = re.compile(
    r"<<<FILE[ \t]*\r?\n(.*?)(?:\r?\n?[ \t]*FILE\s*>{1,3}\s*\}?|(?=\r?\n\s*\{\s*\"action\")|\Z)", re.S)
SR_BLOCK = re.compile(r"<{4,9}[ \t]*SEARCH[ \t]*\r?\n(.*?)\r?\n={4,9}[ \t]*\r?\n(.*?)\r?\n?[ \t]*>{4,9}[ \t]*REPLACE", re.S)
OLD_BLOCK = re.compile(r"<<<OLD[ \t]*\r?\n(.*?)(?:\r?\n?[ \t]*OLD\s*>{1,3}\s*\}?|(?=\r?\n\s*<<<NEW)|\Z)", re.S)
NEW_BLOCK = re.compile(r"<<<NEW[ \t]*\r?\n(.*?)(?:\r?\n?[ \t]*NEW\s*>{1,3}\s*\}?|(?=\r?\n\s*\{\s*\"action\")|\Z)", re.S)
RUN_FALLBACK = re.compile(r'\{\s*"action"\s*:\s*"run"\s*,\s*"cmd"\s*:\s*"(.*)"\s*\}', re.S)
NEXT_ACTION = re.compile(r'\{\s*"action"')


_OLD_KEYS = ("old", "old_text", "old_string", "old_str", "search", "find", "before", "original")
_NEW_KEYS = ("new", "new_text", "new_string", "new_str", "replace", "replacement", "after", "updated")


def _edit_from_fields(obj: dict) -> list[tuple[str, str]]:
    """An edit written as JSON fields. Models use many names (old/new, search/replace, old_string/new_string, even
    the block markers themselves as keys), so match them loosely. Both parts must be present: never guess a deletion."""
    old = new = None
    for k, v in obj.items():
        if not isinstance(v, str):
            continue
        key = k.lower().strip("<>= ")
        if old is None and (key in _OLD_KEYS or "search" in key):
            old = v
        elif new is None and (key in _NEW_KEYS or "replace" in key):
            new = v
    return [(old, new)] if old and new is not None else []


def _parse_actions(text: str) -> list[dict]:
    """Every JSON action in a reply, in order. For `write`, the content comes from the raw <<<FILE ... FILE>>> block
    that follows it (no escaping, so models cannot break it); inline JSON "content" is still accepted."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    dec = json.JSONDecoder(strict=False)  # tolerate raw newlines inside strings
    acts: list[dict] = []
    pos = 0
    while True:
        m = text.find("{", pos)
        if m == -1:
            break
        try:
            obj, end = dec.raw_decode(text[m:])
        except json.JSONDecodeError:
            pos = m + 1
            continue
        if not (isinstance(obj, dict) and "action" in obj):
            pos = m + 1
            continue
        pos = m + end
        if obj["action"] == "write" and not obj.get("content"):
            block = FILE_BLOCK.search(text, pos)
            nxt = NEXT_ACTION.search(text, pos)
            if block and (not nxt or block.start() < nxt.start()):
                obj["content"] = block.group(1).rstrip("\n") + "\n"
                pos = block.end()
        if obj["action"] == "edit" and "edits" not in obj and _edit_from_fields(obj):
            obj["edits"] = _edit_from_fields(obj)
        if obj["action"] == "edit" and "edits" not in obj:
            nxt = NEXT_ACTION.search(text, pos)
            limit = nxt.start() if nxt else len(text)
            pairs, cursor = [], pos
            for blk in SR_BLOCK.finditer(text, pos):
                if blk.start() >= limit:
                    break
                pairs.append((blk.group(1), blk.group(2)))
                cursor = blk.end()
            if not pairs:  # legacy OLD/NEW markers: only accepted when BOTH parts were read
                old_b = OLD_BLOCK.search(text, pos)
                if old_b and old_b.start() < limit:
                    new_b = NEW_BLOCK.search(text, old_b.end())
                    if new_b and new_b.start() < limit:
                        pairs, cursor = [(old_b.group(1), new_b.group(1))], new_b.end()
            obj["edits"] = pairs
            pos = cursor
        if obj["action"] in ("read", "cat", "open", "view") and obj.get("path"):
            obj = {"action": "run", "cmd": "cat -- " + shlex.quote(str(obj["path"]))}
        acts.append(obj)
    if not acts:
        m = RUN_FALLBACK.search(text)
        if m and m.group(1).strip():
            return [{"action": "run", "cmd": m.group(1)}]
    if not acts and '"write"' in text and '\\"' in text:  # models sometimes double-escape quotes inside JSON strings
        fixed = text.replace('\\"', '\\"'[1:])
        if fixed != text:
            return _parse_actions(fixed)
    return acts


def _parse_action(text: str) -> dict | None:
    """The first action in a reply (kept for callers and tests that only need one)."""
    acts = _parse_actions(text)
    return acts[0] if acts else None


def _looks_like_broken_write(text: str) -> bool:
    return bool(re.search(r'"action"\s*:\s*"write"', text))


TRIAGE_PROMPT = """You triage bug reports for an automated bug-fixing agent that works inside one repository.

Issue:
{issue}

Repository snapshot:
{snapshot}

Decide three things.
1. valid: true only if the issue describes ONE specific, observable misbehaviour of code in this repository (what happens versus what should happen, or an error), so a failing test can be written for it. valid is false for requests to find or review bugs in general, new features, questions, or reports too vague to test.
2. difficulty:
   "easy"   the cause is in one function and is visible from the issue (a wrong operator, an off-by-one, a missing case).
   "medium" one file, but the right fix needs reasoning about edge cases or several related behaviours.
   "hard"   the fix needs several coordinated changes or subtle semantics: operator precedence or associativity, time, ordering or concurrency, graph or recursive algorithms, state kept across calls, or the issue gives only a symptom and not the expected behaviour.
3. hint: the files and functions most likely involved and the suspected root cause, in under 60 words.

Reply with ONE JSON object and nothing else:
{{"valid": true, "reason": "<one short sentence>", "difficulty": "easy|medium|hard", "hint": "<text>"}}"""


def _parse_triage(text: str) -> dict:
    """Tolerant parser: anything unreadable falls back to a valid, medium task, i.e. the normal tiered path."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    dec = json.JSONDecoder(strict=False)
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("difficulty" in obj or "valid" in obj):
            diff = str(obj.get("difficulty", "medium")).strip().lower()
            return {"valid": obj.get("valid", True) is not False,
                    "difficulty": diff if diff in ("easy", "medium", "hard") else "medium",
                    "reason": str(obj.get("reason", ""))[:300], "hint": str(obj.get("hint", ""))[:600], "parsed": True}
    return {"valid": True, "difficulty": "medium", "reason": "", "hint": text[:600], "parsed": False}


def triage(llm: LLM, snapshot: str, issue: str) -> dict:
    try:
        text = llm.chat("nano", [{"role": "user", "content": TRIAGE_PROMPT.format(issue=issue, snapshot=snapshot[:9000])}],
                        max_tokens=2500)
    except Exception as e:  # noqa: BLE001
        return {"valid": True, "difficulty": "medium", "reason": "", "hint": f"(triage unavailable: {e})", "parsed": False}
    return _parse_triage(text)


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


NOISE_WORDS = {"demo", "bugfix", "fix", "test", "tests", "example", "sandbox", "copy", "fork", "playground", "repro"}


def related_query(repo_url: str, issue: str) -> str:
    """Project name plus the issue title: what a developer would type to find the discussion of this bug."""
    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    words = [w for w in re.split(r"[-_]", name) if w and w.lower() not in NOISE_WORDS]
    title = (issue.strip().splitlines() or [""])[0][:200]
    return f"{' '.join(words) or name} python {title}".strip()


def summarize_fix(llm: LLM, issue: str, diff: str) -> str:
    """Two plain sentences for the pull request: the root cause and what the patch changes. Nano is cheap and enough."""
    prompt = (f"Bug report title: {issue.strip().splitlines()[0][:200] if issue.strip() else ''}\n\nPatch:\n{diff[:5000]}\n\n"
              "In at most 3 sentences, state the root cause of the bug and what this patch changes to fix it. "
              "Plain text, no preamble, no markdown.")
    try:
        text = llm.chat("nano", [{"role": "user", "content": prompt}], temperature=0.1, max_tokens=900)
    except Exception:  # noqa: BLE001
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return text[:700]


def _auto_finish_act(sb: Sandbox) -> dict | None:
    """If the agent left new test files behind, build the finish action for it so the harness can still check the work.
    The harness verifies the result itself, so this cannot make a wrong fix pass."""
    tests = [f for f in sb.new_files()
             if re.search(r"(^|/)(test_[^/]*|[^/]*_test)\.py$", f) and "test_edge_review_tmp" not in f]
    if not tests:
        return None
    return {"action": "finish", "repro_test_cmd": "python -m pytest -q " + " ".join(tests),
            "suite_cmd": "python -m pytest -q",
            "summary": "Fix verified by the harness; the model ran out of steps before calling finish."}


def _detail(act: dict) -> str:
    a = act.get("action")
    if a == "run":
        return act.get("cmd", "")
    if a == "write":
        return f"{act.get('path', '')}\n{str(act.get('content', ''))[:700]}"
    if a == "edit":
        shown = "\n".join(f"- {o[:300]}\n+ {n[:300]}" for o, n in (act.get("edits") or [])[:2])
        return f"{act.get('path', '')}\n{shown}"
    if a == "search":
        return act.get("query", "")
    return ""


def run_agent(llm: LLM, repo_url: str, issue: str, ref: str | None = None, on_event=lambda e: None,
              tiers=None, post_check=None, route: bool = False) -> Result:
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
        info = triage(llm, snapshot, issue)
        hint = info["hint"]
        emit({"type": "triage", "tier": "nano", "text": hint})
        if route:
            if not info["valid"]:
                emit({"type": "route", "valid": False, "difficulty": info["difficulty"], "tiers": [], "why": info["reason"]})
                res = Result(False, "nano", 0, "rejected: " + (info["reason"] or "not a specific, testable bug report"),
                             "", [], time.time() - t0, None, 0)
                emit({"type": "done", "solved": False, "tier": "nano", "steps": 0, "summary": res.summary, "diff": ""})
                return res
            tiers = ["ultra"] if info["difficulty"] == "hard" else ["super", "ultra"]
            emit({"type": "route", "valid": True, "difficulty": info["difficulty"], "tiers": tiers, "why": info["reason"]})
        state = {"reviewed": False}
        sources: list[dict] = []
        asked: set[str] = set()

        def research(query: str, kind: str) -> str:
            """One Tavily lookup. The query and its sources are recorded for the interface and the pull-request text."""
            if not query or query in asked or len(asked) >= MAX_RESEARCH or not os.getenv("TAVILY_API_KEY"):
                return ""
            asked.add(query)
            results = search(query)
            emit({"type": "research", "kind": kind, "query": query,
                  "sources": [{"title": r["title"], "url": r["url"]} for r in results],
                  "error": "" if results else (getattr(search, "last_error", "") or "no results")})
            sources.extend({"query": query, "title": r["title"], "url": r["url"]} for r in results)
            return format_results(results) if results else ""

        if REMOTE_RESEARCH and repo_url.startswith("http"):  # a real repository: look for related public discussion
            ctx = research(related_query(repo_url, issue), "related discussion")
            if ctx:
                hint += "\n\nPossibly related web results (Tavily; may be about a different problem):\n" + ctx[:1500]
        own = own_modules(sb.host_dir / "repo")
        found = relevant_excerpts(sb.host_dir / "repo", issue)
        excerpts = ("Code most relevant to the issue (found by searching the repository for the names it mentions; "
                    "read these before exploring):\n" + found + "\n\n") if found else ""
        notes = ""
        steps = 0
        bad = 0

        def attempt_finish(tier: str, act: dict, auto: bool = False) -> tuple[Result | None, str]:
            """Run every proof check. Used for the model's own `finish` and for the harness's auto-finish."""
            ok, why = _verify(sb, act)
            emit({"type": "verify", "tier": tier, "ok": ok, "auto": auto, "why": why[:1200]})
            if ok and EDGE_REVIEW and not state["reviewed"]:
                state["reviewed"] = True
                rok, rmsg = _edge_review(llm, tier, sb, issue)
                emit({"type": "review", "tier": tier, "ok": rok, "why": rmsg[:1200]})
                if not rok:
                    ok, why = False, ("An independent edge-case review of your fix found a failing case:\n" + rmsg +
                                      "\nDecide whether your fix is incomplete. If so, fix the source code and finish again. "
                                      "If the review test itself contradicts the issue, finish again unchanged.")
            if not ok:
                return None, why
            patch_text = sb.diff()
            summary = act.get("summary", "")
            if auto:  # the model never explained its fix, so have the cheap model describe it from the diff
                summary = summarize_fix(llm, issue, patch_text) or summary
            res = Result(True, tier, steps, summary, patch_text, list(sources), time.time() - t0, None, bad)
            res.hidden_ok = post_check(sb) if post_check else None
            emit({"type": "done", "solved": True, "tier": tier, "steps": steps, "summary": res.summary, "auto": auto,
                  "diff": res.diff, "repro_cmd": act.get("repro_test_cmd"), "suite_cmd": act.get("suite_cmd")})
            return res, ""

        for ti, tier in enumerate(tiers):
            if ti > 0:
                sb.reset()
            emit({"type": "tier_start", "tier": tier, "model": get_model(tier)})
            first = (f"Issue:\n{issue}\n\nRepository snapshot:\n{snapshot}\n\n{excerpts}"
                     f"Triage hint from a fast model (may be wrong):\n{hint}{notes}")
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": first}]
            recent: list[str] = []
            bad_run = rejects = 0
            abort = ""
            n_steps = STEPS.get(tier, 14)
            for si in range(n_steps):
                u = llm.usage
                if u.prompt_tokens + u.completion_tokens > (TOKEN_BUDGET_REMOTE if repo_url.startswith("http") else TOKEN_BUDGET):
                    emit({"type": "budget", "tier": tier})
                    return Result(False, tier, steps, "stopped: token budget reached", sb.diff(), list(sources), time.time() - t0, None, bad)
                if time.time() - t0 > MAX_SECONDS:
                    emit({"type": "budget", "tier": tier, "reason": "time limit"})
                    return Result(False, tier, steps, "stopped: time limit reached", sb.diff(), list(sources), time.time() - t0, None, bad)
                steps += 1
                reply = llm.chat(tier, _compact(msgs))
                msgs.append({"role": "assistant", "content": reply})
                acts = _parse_actions(reply)[:MAX_ACTIONS]
                if not acts:
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
                sig = json.dumps(acts, sort_keys=True)[:300]
                if recent.count(sig) + 1 >= MAX_SAME_ACTION:
                    abort = f"the same action was repeated {MAX_SAME_ACTION} times"
                    break
                loop_warn = "\n(You repeated an action you already tried; change your approach.)" if recent.count(sig) >= 1 else ""
                left = n_steps - si - 1
                if left <= 2:
                    loop_warn += (f"\n(Only {left} step(s) left. If your fix and your new test pass, call finish NOW "
                                  "instead of polishing; unfinished work counts as a failure.)")
                recent.append(sig)
                parts = []
                for k, act in enumerate(acts):
                    a = act["action"]
                    stop = False
                    if a == "run":
                        code, out = sb.exec(str(act.get("cmd") or "true"))
                        obs = f"exit={code}\n{out}"
                        stop = code != 0
                        found = research(research_query(out, own) or "", "error")
                        if found:
                            obs += "\n\n[Web research via Tavily for this error]\n" + found[:1500]
                    elif a == "write":
                        try:
                            if not act.get("path") or act.get("content") is None:
                                raise ValueError("a write needs a path and the file content between <<<FILE and FILE>>> on the lines after the JSON")
                            sb.write(act["path"], act["content"])
                            obs = f"wrote {act['path']}"
                        except Exception as e:  # noqa: BLE001
                            obs = f"write failed: {e}"
                            stop = True
                    elif a == "edit":
                        try:
                            pairs = act.get("edits") or []
                            if not pairs:
                                raise ValueError("no complete SEARCH/REPLACE block was found, so nothing was changed. Use exactly: the JSON line, then "
                                                 "<<<<<<< SEARCH, the exact old text, =======, the new text, >>>>>>> REPLACE")
                            obs = "; ".join(sb.edit(str(act.get("path") or ""), old, new) for old, new in pairs)
                        except Exception as e:  # noqa: BLE001
                            obs = f"edit failed: {e}"
                            stop = True
                    elif a == "search":
                        obs = research(str(act.get("query") or ""), "asked by the model") or "[no web results: repeated query, limit reached or search unavailable]"
                    elif a == "finish":
                        res, why = attempt_finish(tier, act)
                        if res:
                            return res
                        obs = why
                        rejects += 1
                        stop = True
                    else:
                        obs = f"unknown action {a!r}"
                        stop = True
                    emit({"type": "step", "tier": tier, "action": a, "detail": _detail(act), "obs": obs[:1500]})
                    parts.append(obs if len(acts) == 1 else f"[{k + 1}/{len(acts)} {a}] {obs}")
                    if stop:
                        if k + 1 < len(acts):
                            parts.append(f"(The remaining {len(acts) - k - 1} action(s) in your reply were skipped because of the result above.)")
                        break
                if rejects >= MAX_REJECTS:
                    abort = f"{rejects} rejected finish attempts"
                    break
                feedback = "\n".join(parts)
                if len(feedback) > MAX_FEEDBACK:
                    feedback = feedback[:MAX_FEEDBACK // 2] + "\n...[clipped: read smaller ranges]...\n" + feedback[-MAX_FEEDBACK // 2:]
                msgs.append({"role": "user", "content": feedback + loop_warn})
            if abort:
                emit({"type": "abort", "tier": tier, "reason": abort})
            auto = _auto_finish_act(sb)  # the model may have a working fix but never said "finish"
            if auto:
                res, _ = attempt_finish(tier, auto, auto=True)
                if res:
                    return res
            tried = "\n".join(m["content"][:300] for m in msgs[-5:] if m["role"] == "user")
            notes += (f"\n\nA previous attempt with tier '{tier}' did not succeed ({abort or 'ran out of steps'}). "
                      f"Its last observations were:\n{tried}\nThe repository has been reset to its original state. "
                      "Take a fresh, careful look at the root cause.")
            if ti + 1 < len(tiers):
                emit({"type": "escalate", "from": tier, "to": tiers[ti + 1]})
        res = Result(False, tiers[-1], steps, "unsolved", sb.diff(), list(sources), time.time() - t0, None, bad)
        emit({"type": "done", "solved": False, "tier": tiers[-1], "steps": steps, "summary": "unsolved", "diff": res.diff})
        return res
