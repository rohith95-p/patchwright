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


def own_modules(root: Path) -> set[str]:
    """Top-level module and package names that belong to the project itself."""
    names: set[str] = set()
    for base in (root, root / "src"):
        if not base.is_dir():
            continue
        for p in base.iterdir():
            if p.is_dir() and (p / "__init__.py").exists():
                names.add(p.name)
            elif p.suffix == ".py":
                names.add(p.stem)
    return names


def _enclosing_class(lines: list[str], i: int) -> str | None:
    indent = len(lines[i]) - len(lines[i].lstrip())
    for j in range(i - 1, -1, -1):
        m = re.match(r"(\s*)class\s+(\w+)", lines[j])
        if m and len(m.group(1)) < indent:
            return m.group(2)
    return None


def _block_end(lines: list[str], i: int, cap: int = 70) -> int:
    """Index one past the end of the def/class starting at line i (by indentation), at most `cap` lines long."""
    indent = len(lines[i]) - len(lines[i].lstrip())
    end = i + 1
    while end < len(lines) and end - i < cap:
        s = lines[end]
        if s.strip() and (len(s) - len(s.lstrip())) <= indent and not s.lstrip().startswith((")", "]", "}", "@")):
            break
        end += 1
    return max(end, min(i + 12, len(lines)))


def relevant_excerpts(root: Path, issue: str, max_chars: int = 7000, top: int = 4) -> str:
    """The definitions the issue is about. Names the issue spells exactly (RRCache.popitem) score highest. Plain lowercase
    names (daterange, asciidoc) are found by matching every word of the issue against the project's function and class
    names, keeping only words that point at a handful of symbols so that common words like "format" do not match everything."""
    names = identifiers(issue)
    tokens = {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", issue)} - STOP
    defs = []  # (file, line index, lines, name, class)
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
            if m:
                defs.append((rel, i, lines, m.group(2), _enclosing_class(lines, i) if m.group(1) == "def" else None))
    if not defs:
        return ""
    lower = [d[3].lower().strip("_") for d in defs]
    rare = {}
    for t in tokens:
        n = sum(1 for nl in lower if t == nl or (len(t) >= 6 and t in nl))
        if 0 < n <= 3:
            rare[t] = n
    stems = {d[0].stem.lower() for d in defs}
    hits = []
    for (rel, i, lines, name, cls), nl in zip(defs, lower):
        score = names.get(name, 0) + (names.get(cls, 0) if cls else 0)
        if not score:
            if nl in rare:
                score = 2.5
            elif any(t in nl for t in rare if len(t) >= 6):
                score = 1.5
        if score and rel.stem.lower() in tokens & stems:
            score += 0.5  # the issue also names the module (boltons.timeutils)
        if score and not name.startswith("__"):
            hits.append((score, rel.as_posix(), i, lines))
    out, used, seen = [], 0, set()
    for score, rel, i, lines in sorted(hits, key=lambda h: -h[0]):
        if (rel, i) in seen or len(seen) >= top:
            continue
        seen.add((rel, i))
        chunk = f"--- {rel} (line {i + 1}) ---\n" + "\n".join(lines[max(i - 1, 0):_block_end(lines, i)])
        if used + len(chunk) > max_chars:
            break
        out.append(chunk)
        used += len(chunk)
    return "\n\n".join(out)
