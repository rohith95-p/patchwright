"""Find the code an issue talks about, so the model starts from the right place instead of exploring with `cat`.

On a real repository the biggest early cost is wandering: listing files and reading whole modules. Bug reports usually
name the thing that is broken (`RRCache.popitem()`), so we search the repository for those names and hand the model
the definitions it will have to change. This is plain text search, with no model call and no cost.
"""
import re
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".tox", "docs", "site-packages"}
STOP = {"this", "that", "with", "from", "have", "when", "what", "which", "class", "function", "method", "value", "return", "returns",
        "raise", "raises", "error", "none", "true", "false", "python", "expected", "actual", "result", "test", "tests", "should",
        "documented", "contract", "describe", "reproduction", "steps", "import", "print"}


def identifiers(issue: str) -> dict[str, int]:
    """Names worth searching for, with a weight. The title names the thing that is broken, so its names count most;
    names that only appear in the body (often other classes that are mentioned for comparison) count less."""
    title = issue.strip().splitlines()[0] if issue.strip() else ""
    weights: dict[str, int] = {}

    def add(names, w):
        for n in names:
            if len(n) >= 4 and n.lower() not in STOP:
                weights[n] = max(weights.get(n, 0), w)

    add(re.findall(r"[A-Za-z_]\w+", " ".join(re.findall(r"`([^`\n]+)`", issue))), 1)
    add(re.findall(r"\b[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+\b", issue), 1)   # CamelCase
    add(re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", issue), 1)                  # snake_case
    add(re.findall(r"[A-Za-z_]\w+", title), 3)                                # anything in the title
    return weights


def _enclosing_class(lines: list[str], i: int) -> str | None:
    indent = len(lines[i]) - len(lines[i].lstrip())
    for j in range(i - 1, -1, -1):
        m = re.match(r"(\s*)class\s+(\w+)", lines[j])
        if m and len(m.group(1)) < indent:
            return m.group(2)
    return None


def relevant_excerpts(root: Path, issue: str, max_chars: int = 6000, window: int = 40, top: int = 3) -> str:
    names = identifiers(issue)
    if not names:
        return ""
    hits = []
    for p in root.rglob("*.py"):
        rel = p.relative_to(root)
        if set(rel.parts) & SKIP_DIRS or "test" in p.name.lower() or any("test" in part.lower() for part in rel.parts[:-1]):
            continue
        try:
            if p.stat().st_size > 600_000:
                continue
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines):
            m = re.match(r"\s*(class|def)\s+(\w+)", line)
            if not m or m.group(2) not in names:
                continue
            kind, name = m.groups()
            cls = _enclosing_class(lines, i) if kind == "def" else None
            score = names[name] + (names.get(cls, 0) if cls else 0)
            hits.append((score, rel.as_posix(), i, lines))
    out, used, seen = [], 0, set()
    for score, rel, i, lines in sorted(hits, key=lambda h: -h[0]):
        if (rel, i) in seen or len(seen) >= top:
            continue
        seen.add((rel, i))
        start = max(i - 1, 0)
        chunk = f"--- {rel} (line {i + 1}) ---\n" + "\n".join(lines[start:i + window])
        if used + len(chunk) > max_chars:
            break
        out.append(chunk)
        used += len(chunk)
    return "\n\n".join(out)
