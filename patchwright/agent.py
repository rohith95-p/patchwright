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
from .context import identifiers, own_modules, relevant_excerpts
from .gates import run_gates
from .llm import LLM
from .sandbox import Sandbox
from .tools import docs_search, format_results, research_query, search

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
GROUNDING = os.getenv("TAVILY_GROUNDING", "1") == "1"  # look up the documented behaviour of the API being fixed (real repositories only)
REMOTE_RESEARCH = os.getenv("TAVILY_CONTEXT", "1") == "1"  # look up related discussion for real remote repositories
MAX_FEEDBACK = 12000  # characters of results returned to the model per reply (real repositories have huge files)
MAX_ACTIONS = 3      # actions executed per model reply; each extra action saves a whole model call
KEEP_FULL = 6        # most recent messages kept verbatim; older ones are clipped to save tokens

REVIEW_PROMPT = """A fix was made for this bug report.

Issue:
{issue}

Patch:
{diff}
{docs}
Write a short pytest file that probes edge cases this patch might still get wrong. Use ONLY behaviour the issue states or that is obviously correct for the function (empty input, single element, boundaries, repeated or adjacent separators, input already in the target form, order independence). Do not invent new requirements. Reply with ONLY the Python source of the test file, no explanation and no markdown fences. Import from the modules shown in the patch."""

SYSTEM = """You are Patchwright, an expert software engineer fixing a bug in a repository mounted at /work in a Linux sandbox (Python 3.11, bash, pytest installed; there is no git inside the sandbox).

You are given a snapshot of the repository below, so you usually do not need to list or cat files. Work like this:
1. Understand the issue and find the root cause in the code.
2. Write a NEW test file (never edit existing test files) that reproduces the bug, and run it to see it fail.
3. Fix the SOURCE code with a minimal, correct change that addresses the root cause, not just the example in the issue.
4. Run your new test and the whole existing suite; both must pass.
5. Finish.

Reply with exactly ONE JSON object per turn and nothing else. The action value must be one of: run, write, edit, search, finish (to read or search files, use run with grep or sed). Actions:
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


TOOLS = [
    {"type": "function", "function": {"name": "run", "description": "Run a bash command in /work: read code (grep -n, sed -n 'A,Bp' file), run tests, run python.",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
    {"type": "function", "function": {"name": "write", "description": "Create a NEW file (for example your new test). Never use it on an existing source file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                                                        "content": {"type": "string", "description": "the full file content, exactly as it should appear on disk"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit", "description": "Change an EXISTING file: replace the exact text `search` (copied from the file, unique in it, "
                                                                     "including indentation) with `replace`.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "search": {"type": "string"}, "replace": {"type": "string"}},
                       "required": ["path", "search", "replace"]}}},
    {"type": "function", "function": {"name": "search", "description": "Web search, only for unfamiliar library or error behaviour.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "finish", "description": "Call when your fix and your new test pass. The harness then verifies everything independently.",
        "parameters": {"type": "object", "properties": {
            "repro_test_cmd": {"type": "string", "description": "command that runs ONLY your new test, e.g. python -m pytest -q test_bug.py"},
            "suite_cmd": {"type": "string", "description": "command that runs the project's full suite, e.g. python -m pytest -q"},
            "summary": {"type": "string", "description": "root cause and fix in 2-4 sentences"}}, "required": ["repro_test_cmd", "suite_cmd", "summary"]}}},
]


def _call_to_act(c: dict) -> dict:
    """A native tool call as the internal action the loop already executes."""
    a, args = c["name"], c["args"]
    if a == "edit":
        return {"action": "edit", "path": args.get("path", ""), "edits": [(args["search"], args["replace"])] if "search" in args and "replace" in args else []}
    return {"action": a, **args}


_i = SYSTEM.index("Reply with exactly ONE JSON object")
_j = SYSTEM.index("Read files in small ranges")
SYSTEM_TOOLS = (SYSTEM[:_i] + "Act only by calling the tools; never answer in prose. You may call up to 3 tools in one reply (they run in order; if one "
                "fails, the rest are skipped): batch what does not depend on a result you must read first, for example write your new test and run it, "
                "or edit the source and run the new test and the suite. NEVER rewrite an existing source file with write; use edit.\n\n" + SYSTEM[_j:])


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


READ_NAMES = {"read", "cat", "open", "view", "show", "print", "read_file", "readfile", "file_read", "head", "tail", "view_file", "open_file"}
SHELL_NAMES = {"bash", "sh", "shell", "exec", "execute", "command", "cmd", "terminal", "run_command", "run_shell", "run_bash", "shell_command"}
GREP_NAMES = {"grep", "rg", "ripgrep", "search_code", "find_in_files", "search_files", "grep_search"}
LIST_NAMES = {"ls", "find", "tree", "list", "list_files", "list_dir", "listdir"}
REPLACE_NAMES = {"replace", "str_replace", "replace_in_file", "str_replace_editor", "update_file", "modify", "apply_edit"}
VALID_ACTIONS = "run, write, edit, search, finish"
READ_ONLY = re.compile(r"^\s*(cd\s+\S+\s*(&&|;)\s*)?(cat|sed|grep|egrep|rg|ls|head|tail|find|wc|file|tree|nl|awk)\b")
NUDGE_AFTER = 3   # read-only replies in a row before the harness tells the model to stop exploring and write the test
PUSH_AFTER = 5    # ... and before it tells the model to commit to a fix


def is_reading(acts: list[dict]) -> bool:
    """True when every action in a reply only looks at the repository (nothing was written, edited or tested)."""
    return bool(acts) and all((a["action"] == "run" and bool(READ_ONLY.match(str(a.get("cmd", ""))))) or a["action"] == "search" for a in acts)


def _first(obj: dict, *keys):
    for k in keys:
        v = obj.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _normalize_action(obj: dict) -> dict:
    """Models invent action names (read, grep, bash, sed, str_replace ...) and field names. Map the ones whose meaning is
    obvious onto the real actions, so a sensible intention is not wasted on a format error."""
    a = str(obj.get("action", "")).strip().lower()
    path = _first(obj, "path", "file", "file_path", "filename", "filepath", "target")
    if a in READ_NAMES and path:
        start, end = _first(obj, "start", "start_line", "from"), _first(obj, "end", "end_line", "to")
        rng = _first(obj, "lines", "range", "line_range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            start, end = rng
        q = shlex.quote(str(path))
        if isinstance(start, int) and isinstance(end, int):
            return {"action": "run", "cmd": f"sed -n '{start},{end}p' -- {q}"}
        return {"action": "run", "cmd": "cat -- " + q}
    if a in SHELL_NAMES or (a == "sed" or a == "run") and not obj.get("cmd") and _first(obj, "command", "script", "code"):
        cmd = _first(obj, "cmd", "command", "script", "code", "input")
        if isinstance(cmd, list):
            cmd = " ".join(shlex.quote(str(c)) for c in cmd)
        if cmd:
            return {"action": "run", "cmd": str(cmd)}
    if a in GREP_NAMES:
        pat = _first(obj, "pattern", "query", "regex", "text", "term")
        if pat:
            return {"action": "run", "cmd": f"grep -rn --include='*.py' -- {shlex.quote(str(pat))} {shlex.quote(str(path or '.'))} | head -40"}
    if a in LIST_NAMES:
        return {"action": "run", "cmd": f"ls -la {shlex.quote(str(path or '.'))}"}
    if a in REPLACE_NAMES and path:
        old = _first(obj, "old_str", "old_string", "old", "search", "find", "before")
        new = obj.get("new_str", obj.get("new_string", obj.get("new", obj.get("replace", obj.get("replacement", obj.get("after"))))))
        if isinstance(old, str) and isinstance(new, str):
            return {"action": "edit", "path": str(path), "edits": [(old, new)]}
    return obj



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
        obj = _normalize_action(obj)
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


SUITE_CMD = "python -m pytest -q -p no:cacheprovider -rfE 2>&1 | tail -n 250"


def _failing_ids(out: str) -> set[str]:
    return set(re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M))


def _baseline(sb: Sandbox) -> tuple[int, set[str]]:
    """The project's suite before any change. Real projects often ship with failing or uncollectable tests (old code on a new
    Python, optional dependencies); a fix is judged on the failures it adds, not on failures that were already there."""
    code, out = sb.exec(SUITE_CMD, timeout=600)
    return code, _failing_ids(out)


def _verify(sb: Sandbox, act: dict, baseline: tuple[int, set[str]] | None = None) -> tuple[bool, str]:
    repro, suite = act.get("repro_test_cmd", ""), act.get("suite_cmd", "")
    if not repro or not suite:
        return False, "finish needs both repro_test_cmd and suite_cmd."
    ok, why = sb.prove_fails_without_fix(repro)
    if not ok:
        return False, why
    code, out = sb.exec(repro)
    if code != 0:
        return False, f"Repro test still fails:\n{out}"
    if baseline is None:
        code, out = sb.exec(suite, timeout=600)
        if code != 0:
            return False, f"Existing suite fails:\n{out}"
        return True, "verified"
    base_code, base_ids = baseline
    code, out = sb.exec(SUITE_CMD, timeout=600)
    fresh = {n for n in sb.new_files()}
    added = {i for i in _failing_ids(out) - base_ids if i.split("::")[0] not in fresh}
    if added or (code != 0 and base_code == 0 and not _failing_ids(out)):
        return False, "Your change broke tests that passed before it:\n" + "\n".join(sorted(added)[:15]) + "\n\n" + out[-1500:]
    note = f" ({len(base_ids)} test(s) were already failing before any change and are ignored)" if base_ids else ""
    return True, "verified" + note


def _edge_review(llm: LLM, tier: str, sb: Sandbox, issue: str, docs: str = "") -> tuple[bool, str]:
    """One cheap extra call: a reviewer writes edge-case tests for the patch; the harness runs them."""
    try:
        ref = ("\nOfficial documentation found on the web (use it for the expected behaviour if it applies to this code):\n"
               + docs[:1200] + "\n") if docs else ""
        code = llm.chat(tier, [{"role": "user", "content": REVIEW_PROMPT.format(issue=issue, diff=sb.diff()[:6000], docs=ref)}],
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
        c = m.get("content") or ""
        out.append({**m, "content": (c if len(c) <= 350 else c[:300] + " ...[clipped]") or None})
    return out + msgs[-KEEP_FULL:]


NOISE_WORDS = {"demo", "bugfix", "fix", "test", "tests", "example", "sandbox", "copy", "fork", "playground", "repro"}


def grounding_query(repo_url: str, issue: str) -> str:
    """What the official documentation says about the thing that is broken: the names in the issue title, plus the library."""
    names = [n for n, w in identifiers(issue).items() if w >= 3][:4]
    if not names:
        return ""
    lib = ""
    if repo_url.startswith("http"):  # the project's name without words like "demo" or "bugfix" (a fork's name is not the library's)
        raw = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
        lib = " ".join(w for w in re.split(r"[-_]", raw) if w and w.lower() not in NOISE_WORDS)
    return f"python {lib} {' '.join(names)} documented behavior".replace("  ", " ")


def related_query(repo_url: str, issue: str) -> str:
    """Project name plus the issue title: what a developer would type to find the discussion of this bug."""
    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    words = [w for w in re.split(r"[-_]", name) if w and w.lower() not in NOISE_WORDS]
    title = (issue.strip().splitlines() or [""])[0][:200]
    return f"{' '.join(words) or name} python {title}".strip()


def summarize_fix(llm: LLM, issue: str, diff: str, tier: str = "nano") -> str:
    """Two or three plain sentences for the pull request: the root cause and what the patch changes. It is written by the
    model that solved the task, from the issue and the patch, so the explanation matches what was actually fixed."""
    prompt = (f"Bug report:\n{issue.strip()[:1500]}\n\nPatch:\n{diff[:5000]}\n\n"
              "In at most 3 sentences, state the root cause of the bug and what this patch changes to fix it. "
              "Be precise and do not claim anything the patch does not do. Plain text, no preamble, no markdown.")
    try:
        text = llm.chat(tier, [{"role": "user", "content": prompt}], temperature=0.1, max_tokens=900)
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


def run_agent(llm: LLM, repo_url: str, issue: str, ref: str | None = None, on_event=lambda e: None, source_url: str | None = None,
              tiers=None, post_check=None, route: bool = False) -> Result:
    t0 = time.time()
    tiers = tiers or TIER_ORDER

    def emit(ev: dict) -> None:
        u = llm.usage
        ev["t"] = round(time.time() - t0, 1)
        ev["usage"] = {"calls": u.calls, "tokens": u.prompt_tokens + u.completion_tokens,
                       "cost_usd": round(u.cost_usd, 6), "by_tier": u.by_tier}
        on_event(ev)

    real_url = source_url or repo_url  # the public project this copy came from, if the working copy is local
    emit({"type": "start", "repo": real_url, "tiers": tiers, "models": {t: get_model(t) for t in ["nano", *tiers]}})
    with Sandbox(repo_url, ref) as sb:
        sb.setup()
        baseline = _baseline(sb)
        emit({"type": "baseline", "suite_exit": baseline[0], "failing": len(baseline[1])})
        snapshot = sb.snapshot(budget=6000 if real_url.startswith("http") else 14000)  # a big real project: the tree and the relevant code, not file dumps
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
        state = {"reviewed": False, "gated": False}
        sources: list[dict] = []
        asked: set[str] = set()

        def research(query: str, kind: str, fn=search) -> str:
            """One Tavily lookup. The query and its sources are recorded for the interface and the pull-request text."""
            if not query or query in asked or len(asked) >= MAX_RESEARCH or not os.getenv("TAVILY_API_KEY"):
                return ""
            asked.add(query)
            results = fn(query)
            emit({"type": "research", "kind": kind, "query": query,
                  "sources": [{"title": r["title"], "url": r["url"]} for r in results],
                  "error": "" if results else (getattr(fn, "last_error", "") or "no results")})
            sources.extend({"query": query, "title": r["title"], "url": r["url"]} for r in results)
            return format_results(results) if results else ""

        if REMOTE_RESEARCH and real_url.startswith("http"):  # a real repository: look for related public discussion
            ctx = research(related_query(real_url, issue), "related discussion")
            if ctx:
                hint += "\n\nPossibly related web results (Tavily; may be about a different problem):\n" + ctx[:1500]
        docs_ctx = ""
        if GROUNDING and real_url.startswith("http"):  # the documented behaviour of what is being fixed, from official documentation only
            docs_ctx = research(grounding_query(real_url, issue), "documentation", docs_search)
            if docs_ctx:
                hint += ("\n\nOfficial documentation found by Tavily (use it for expected behaviour; "
                         "ignore it if it is about something else):\n" + docs_ctx[:1200])
        own = own_modules(sb.host_dir / "repo")
        found = relevant_excerpts(sb.host_dir / "repo", issue)
        excerpts = ("Code most relevant to the issue (found by searching the repository for the names it mentions; "
                    "read these before exploring):\n" + found + "\n\n") if found else ""
        notes = ""
        tool_mode = hasattr(llm, "chat_tools") and os.getenv("PW_TOOLS", "1") == "1"
        steps = 0
        bad = 0

        def attempt_finish(tier: str, act: dict, auto: bool = False) -> tuple[Result | None, str]:
            """Run every proof check. Used for the model's own `finish` and for the harness's auto-finish."""
            ok, why = _verify(sb, act, baseline)
            emit({"type": "verify", "tier": tier, "ok": ok, "auto": auto, "why": why[:1200]})
            if ok and EDGE_REVIEW and not state["reviewed"]:
                state["reviewed"] = True
                rok, rmsg = _edge_review(llm, tier, sb, issue, docs_ctx)
                emit({"type": "review", "tier": tier, "ok": rok, "why": rmsg[:1200]})
                if not rok:
                    ok, why = False, ("An independent edge-case review of your fix found a failing case:\n" + rmsg +
                                      "\nDecide whether your fix is incomplete. If so, fix the source code and finish again. "
                                      "If the review test itself contradicts the issue, finish again unchanged.")
            if ok:  # the project's own formatters/linters/type checkers, on the lines this patch touched
                try:
                    gok, gmsg, gtools = run_gates(sb)
                except Exception as e:  # noqa: BLE001  a broken gate must never sink a verified fix
                    gok, gmsg, gtools = True, "", []
                    emit({"type": "gates", "ok": True, "tools": [], "why": f"skipped: {e}"[:300]})
                if gtools:
                    emit({"type": "gates", "ok": gok, "tools": gtools, "why": gmsg[:1200]})
                    code, out = sb.exec(act.get("suite_cmd", ""), timeout=600)
                    if code != 0:
                        ok, why = False, "Existing suite fails after auto-formatting:\n" + out
                    elif not gok and not state.get("gated"):
                        state["gated"] = True
                        ok, why = False, ("The project's own checks (CI would run them) flag lines your patch added:\n" + gmsg +
                                          "\nFix these in the source, then finish again.")
            if not ok:
                return None, why
            patch_text = sb.diff()
            summary = act.get("summary", "")
            if auto:  # the model never explained its fix, so have it describe the patch from the diff
                summary = summarize_fix(llm, issue, patch_text, tier) or summary
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
                     f"Triage hint from a fast model (may be wrong):\n{hint}{notes}\n\n"
                     "Plan: the code above is where to look. In your FIRST reply, call tools to write a NEW test that reproduces the issue's own "
                     "example and run it (read a file only if the code above is not enough). In your SECOND reply, edit the source to fix the root "
                     "cause and run the new test and the suite. Do not read whole files.")
            msgs = [{"role": "system", "content": SYSTEM_TOOLS if tool_mode else SYSTEM}, {"role": "user", "content": first}]
            recent: list[str] = []
            read_streak = 0
            bad_run = rejects = 0
            abort = ""
            n_steps = STEPS.get(tier, 14)
            for si in range(n_steps):
                u = llm.usage
                if u.prompt_tokens + u.completion_tokens > (TOKEN_BUDGET_REMOTE if real_url.startswith("http") else TOKEN_BUDGET):
                    emit({"type": "budget", "tier": tier})
                    return Result(False, tier, steps, "stopped: token budget reached", sb.diff(), list(sources), time.time() - t0, None, bad)
                if time.time() - t0 > MAX_SECONDS:
                    emit({"type": "budget", "tier": tier, "reason": "time limit"})
                    return Result(False, tier, steps, "stopped: time limit reached", sb.diff(), list(sources), time.time() - t0, None, bad)
                steps += 1
                calls = []
                if tool_mode:  # native tool calling: the API delivers well-formed actions, so there is no format to get wrong
                    out_msg = llm.chat_tools(tier, _compact(msgs), TOOLS)
                    reply, calls = out_msg["content"], out_msg["calls"]
                    msgs.append({"role": "assistant", "content": reply or None, "tool_calls": [
                        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["raw"]}} for c in calls]} if calls
                        else {"role": "assistant", "content": reply or "(no reply)"})
                    acts = [_call_to_act(c) for c in calls[:MAX_ACTIONS]]
                else:
                    reply = llm.chat(tier, _compact(msgs))
                    msgs.append({"role": "assistant", "content": reply})
                    acts = _parse_actions(reply)[:MAX_ACTIONS]
                if not acts:
                    bad += 1
                    bad_run += 1
                    emit({"type": "bad_reply", "tier": tier, "head": reply[:200], "finish": getattr(llm, "last_finish", "")})
                    if bad_run >= MAX_BAD_CONSEC:
                        abort = f"{bad_run} unparseable replies in a row"
                        break
                    hint = ("Call one of the tools (run, write, edit, search, finish). Do not answer in prose." if tool_mode else
                            "Your write action could not be parsed. Send the JSON line with only action and path, then the file "
                            "content raw between <<<FILE and FILE>>>.") if (tool_mode or _looks_like_broken_write(reply)) else "Reply with one valid JSON action object."
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
                parts, obs_raw = [], []
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
                        obs = (f"unknown action {a!r}. The only actions are: {VALID_ACTIONS}. To read or search files use run with a shell command, "
                               "e.g. {\"action\":\"run\",\"cmd\":\"grep -n chunked boltons/iterutils.py\"} or {\"action\":\"run\",\"cmd\":\"sed -n '100,160p' boltons/iterutils.py\"}.")
                        stop = True
                    emit({"type": "step", "tier": tier, "action": a, "detail": _detail(act), "obs": obs[:1500]})
                    obs_raw.append(obs)
                    parts.append(obs if len(acts) == 1 else f"[{k + 1}/{len(acts)} {a}] {obs}")
                    if stop:
                        if k + 1 < len(acts):
                            parts.append(f"(The remaining {len(acts) - k - 1} action(s) in your reply were skipped because of the result above.)")
                        break
                if rejects >= MAX_REJECTS:
                    abort = f"{rejects} rejected finish attempts"
                    break
                read_streak = read_streak + 1 if is_reading(acts) else 0
                if read_streak == NUDGE_AFTER:
                    loop_warn += (f"\n(You have only been reading for {read_streak} steps. Stop exploring. In your NEXT reply, write a NEW test "
                                  "file that reproduces the issue (use the issue's own example) and run it, as two actions in one reply.)")
                elif read_streak >= PUSH_AFTER and (read_streak - PUSH_AFTER) % 3 == 0:
                    loop_warn += (f"\n(You have read for {read_streak} steps without changing anything. You now know enough: edit the source with "
                                  "your best fix, add your test, and run both in one reply. A wrong attempt that fails teaches more than more reading.)")
                if tool_mode:  # every tool call needs its own result message, in order
                    cap = max(1500, MAX_FEEDBACK // max(1, len(calls)))
                    for i, c in enumerate(calls):
                        if i < len(obs_raw):
                            text = obs_raw[i]
                        elif i >= MAX_ACTIONS:
                            text = f"(skipped: at most {MAX_ACTIONS} actions per reply)"
                        else:
                            text = "(skipped because an earlier action failed)"
                        if len(text) > cap:
                            text = text[:cap // 2] + "\n...[clipped: read smaller ranges]...\n" + text[-(cap // 2):]
                        msgs.append({"role": "tool", "tool_call_id": c["id"], "content": text + (loop_warn if i == len(calls) - 1 else "")})
                    continue
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
            tried = "\n".join((m.get("content") or "")[:300] for m in msgs[-5:] if m["role"] in ("user", "tool"))
            notes += (f"\n\nA previous attempt with tier '{tier}' did not succeed ({abort or 'ran out of steps'}). "
                      f"Its last observations were:\n{tried}\nThe repository has been reset to its original state. "
                      "Take a fresh, careful look at the root cause.")
            if ti + 1 < len(tiers):
                emit({"type": "escalate", "from": tier, "to": tiers[ti + 1]})
        res = Result(False, tiers[-1], steps, "unsolved", sb.diff(), list(sources), time.time() - t0, None, bad)
        emit({"type": "done", "solved": False, "tier": tiers[-1], "steps": steps, "summary": "unsolved", "diff": res.diff})
        return res
