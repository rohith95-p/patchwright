"""The project's own quality gates: formatters, linters and type checkers, run on what the patch changed.

A fix that passes the tests but fails the project's CI (import order, formatting, types) would be bounced by any maintainer.
So Patchwright finds the tools the project uses, auto-fixes what is safe to fix (formatting, import order), re-checks, and
reports remaining problems ONLY on the lines the patch added or changed, so it is never blamed for old issues.
"""
import re
import shlex
from pathlib import Path

# tool -> how it is detected, fixed and checked. {f} is the list of changed Python files.
GATES = {
    "ruff": {"fix": ["ruff check --fix --quiet --ignore EXE001,EXE002 {f}", "ruff format --quiet {f}"],
             "check": "ruff check --output-format=concise --ignore EXE001,EXE002 {f}", "pip": "ruff"},
    "black": {"fix": ["black -q {f}"], "check": None, "pip": "black"},
    "flake8": {"fix": [], "check": "flake8 {f}", "pip": "flake8"},
    "pyright": {"fix": [], "check": "pyright {f}", "pip": "pyright"},
    "mypy": {"fix": [], "check": "mypy --no-error-summary --ignore-missing-imports {f}", "pip": "mypy"},
}
CONFIG_FILES = ["pyproject.toml", "tox.ini", "setup.cfg", ".pre-commit-config.yaml", ".flake8", "ruff.toml", ".ruff.toml",
                "pyrightconfig.json", "mypy.ini", "Makefile", "noxfile.py"]
LOCATION = re.compile(r"^\s*(?P<path>[^\s:]+\.py):(?P<line>\d+)")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")


def detect_tools(root: Path) -> list[str]:
    """Which of the known tools this project uses, judged by its configuration files and CI workflows."""
    texts = []
    for name in CONFIG_FILES:
        f = root / name
        if f.is_file():
            texts.append(f.read_text(encoding="utf-8", errors="replace"))
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        texts += [p.read_text(encoding="utf-8", errors="replace") for p in wf.glob("*.y*ml")]
    blob = "\n".join(texts).lower()
    found = [t for t in GATES if re.search(rf"\b{t}\b", blob)]
    if "ruff" in found and "black" in found:  # ruff format and black do the same job; do not run both
        found.remove("black")
    return found


def changed_ranges(diff_u0: str) -> dict[str, list[tuple[int, int]]]:
    """File -> line ranges the patch added or changed (from a zero-context diff)."""
    out: dict[str, list[tuple[int, int]]] = {}
    current = None
    for line in diff_u0.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            out.setdefault(current, [])
        elif current and (m := HUNK.match(line)):
            start, count = int(m.group("start")), int(m.group("count") if m.group("count") is not None else 1)
            if count:
                out[current].append((start, start + count - 1))
    return out


def on_changed_lines(output: str, ranges: dict[str, list[tuple[int, int]]]) -> list[str]:
    """Diagnostics from a tool's output that point at a line this patch changed. Multi-line diagnostics keep their first line."""
    bad = []
    for line in output.splitlines():
        m = LOCATION.match(line)
        if not m:
            continue
        path = m.group("path").removeprefix("/work/").removeprefix("./")
        n = int(m.group("line"))
        if any(a <= n <= b for a, b in ranges.get(path, [])):
            bad.append(line.strip()[:240])
    return bad


def run_gates(sb) -> tuple[bool, str, list[str]]:
    """(ok, problems to show the model, tools that ran). A project with no known tools passes untouched."""
    tools = detect_tools(sb.host_dir / "repo")
    files = sb.changed_py_files()
    if not tools or not files:
        return True, "", []
    fl = " ".join(shlex.quote(f) for f in files)
    for t in tools:
        sb.exec(f"pip install -q {GATES[t]['pip']} 2>&1 | tail -1", timeout=900)
    for t in tools:
        for cmd in GATES[t]["fix"]:
            sb.exec(cmd.format(f=fl), timeout=300)
    ranges = changed_ranges(sb.diff_zero_context())
    problems = []
    for t in tools:
        check = GATES[t]["check"]
        if check:
            _, out = sb.exec(check.format(f=fl), timeout=900)
            problems += [f"[{t}] {d}" for d in on_changed_lines(out, ranges)]
    return not problems, "\n".join(problems[:20]), tools
