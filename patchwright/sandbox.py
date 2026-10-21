"""Docker sandbox: the repo lives in a throwaway container; the agent never touches the host."""
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

BASE_IMAGE = "python:3.11-slim"
IMAGE = "patchwright-sandbox:py311-2"  # BASE_IMAGE plus pytest, built once so no run pays a ~40s pip install
DOCKERFILE = (f"FROM {BASE_IMAGE}\nRUN apt-get update -qq && apt-get install -y -qq --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*\n"
              "RUN pip install --no-cache-dir pytest\n")
MAX_OUT = 6000
_image_lock = threading.Lock()


def _run(args: list[str], timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired:
        return 124, f"[timeout after {timeout}s]"


def _clip(s: str) -> str:
    return s if len(s) <= MAX_OUT else s[: MAX_OUT // 2] + "\n...[clipped]...\n" + s[-MAX_OUT // 2 :]


def ensure_image() -> str:
    """Build the sandbox image the first time it is needed. Falls back to the plain base image if the build fails."""
    with _image_lock:
        if _run(["docker", "image", "inspect", IMAGE], 30)[0] == 0:
            return IMAGE
        try:
            p = subprocess.run(["docker", "build", "-q", "-t", IMAGE, "-"], input=DOCKERFILE, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=600)
            if p.returncode == 0:
                return IMAGE
        except subprocess.TimeoutExpired:
            pass
        return BASE_IMAGE


class Sandbox:
    def __init__(self, repo_url: str, ref: str | None = None) -> None:
        self.image = ensure_image()
        self.name = f"patchwright-{uuid.uuid4().hex[:8]}"
        self.host_dir = Path(tempfile.mkdtemp(prefix="pw-"))
        code, out = _run(["git", "clone", "--quiet", repo_url, str(self.host_dir / "repo")], 300)
        if code:
            raise RuntimeError(f"git clone failed: {out}")
        if ref:
            _run(["git", "-C", str(self.host_dir / "repo"), "checkout", "--quiet", ref], 60)
        code, out = _run(
            ["docker", "run", "-d", "--name", self.name, "--memory", "2g", "--cpus", "2",
             "-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{self.host_dir / 'repo'}:/work", "-w", "/work", self.image, "sleep", "infinity"],
            120,
        )
        if code:
            raise RuntimeError(f"docker run failed: {out}")

    def setup(self) -> None:
        """pytest is already in the sandbox image; install it only if we had to fall back to the base image."""
        if self.exec("python -m pytest --version", timeout=60)[0] != 0:
            self.exec("pip install -q pytest 2>&1 | tail -1", timeout=240)
        # A real project must be importable for its tests to run: install it in editable mode, best effort.
        if self.exec("test -f pyproject.toml -o -f setup.py", timeout=20)[0] == 0:
            self.exec("pip install -q -e . 2>&1 | tail -2", timeout=300)
        # Older projects ship conftest hooks written for an older pytest ("declared in the hookimpl but can not be found in the
        # hookspec"); a current pytest cannot even collect their tests. Fall back to the last pytest that still accepts them.
        if "hookimpl" in self.exec("python -m pytest --collect-only -q 2>&1 | tail -5", timeout=120)[1]:
            self.exec("pip install -q 'pytest<8' 2>&1 | tail -1", timeout=240)

    def prove_fails_without_fix(self, repro_cmd: str) -> tuple[bool, str]:
        """Revert changes to tracked source files (keeping new test files), run the repro test, restore the fix."""
        repo = str(self.host_dir / "repo")
        # filter M = tracked files that existed at HEAD and were modified. New files (even ones git was told to track
        # with add -N when the diff was shown) are NOT in this list, so the agent's own new tests are never mistaken
        # for edited existing tests.
        names = self._changed(repo, "M")
        gone = self._changed(repo, "D")
        if not names:
            return False, "No change to existing source files was found; a fix must modify the code."
        edited_tests = [n for n in names + gone if "test" in Path(n).name.lower()]
        if edited_tests:
            return False, f"You modified existing test files ({', '.join(edited_tests)}). Revert them and put new tests in a NEW file."
        code, patch = _run(["git", "-C", repo, "diff", "--binary", "--", *names], 60)
        patch_file = self.host_dir / "fix.patch"
        patch_file.write_text(patch, encoding="utf-8", newline="")
        code, err = _run(["git", "-C", repo, "checkout", "--", *names], 60)
        if code:
            return False, f"internal error reverting fix: {err}"
        try:
            code, out = self.exec(repro_cmd)
        finally:
            rc, err = _run(["git", "-C", repo, "apply", "--whitespace=nowarn", str(patch_file)], 60)
            if rc:
                raise RuntimeError(f"internal error restoring fix: {err}")
        if code == 0:
            return False, f"Repro test passes even WITHOUT your fix, so it does not reproduce the bug:\n{out}"
        return True, out

    @staticmethod
    def _changed(repo: str, kind: str) -> list[str]:
        out = _run(["git", "-C", repo, "diff", "--name-only", f"--diff-filter={kind}"], 60)[1]
        return [n for n in out.splitlines() if n and not n.startswith("warning")]

    def new_files(self) -> list[str]:
        """Files the agent created (untracked, or added with add -N when a diff was shown)."""
        repo = str(self.host_dir / "repo")
        untracked = _run(["git", "-C", repo, "ls-files", "--others", "--exclude-standard"], 60)[1].splitlines()
        return sorted({n for n in untracked + self._changed(repo, "A") if n and not n.startswith("warning")})

    def reset(self) -> None:
        """Restore the repo to its original commit (used when escalating to a stronger model)."""
        repo = str(self.host_dir / "repo")
        _run(["git", "-C", repo, "reset", "-q"], 60)  # drop add -N entries so clean can remove those new files
        _run(["git", "-C", repo, "checkout", "--", "."], 60)
        _run(["git", "-C", repo, "clean", "-fdq"], 60)

    def snapshot(self, budget: int = 14000, per_file: int = 3500) -> str:
        """File tree plus contents of small text files (source before tests), for the model's first prompt."""
        root = self.host_dir / "repo"
        skip = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache"}
        files = [p for p in root.rglob("*") if p.is_file() and not (set(p.relative_to(root).parts) & skip)]
        rel = sorted(p.relative_to(root).as_posix() for p in files)[:300]
        text_ext = {".py", ".md", ".txt", ".cfg", ".toml", ".ini", ".json", ".yml", ".yaml", ".js", ".ts", ".c", ".h"}
        cands = [p for p in files if p.suffix in text_ext and p.stat().st_size < 60000]
        cands.sort(key=lambda p: ("test" in p.name.lower(), p.stat().st_size))
        nl = chr(10)
        out, used = ["FILES:" + nl + nl.join(rel)], 0
        for p in cands:
            try:
                body = p.read_text(encoding="utf-8")[:per_file]
            except (UnicodeDecodeError, OSError):
                continue
            chunk = f"{nl}--- {p.relative_to(root).as_posix()} ---{nl}{body}"
            if used + len(chunk) > budget:
                break
            out.append(chunk)
            used += len(chunk)
        return "".join(out)

    def exec(self, cmd: str, timeout: int = 180) -> tuple[int, str]:
        code, out = _run(["docker", "exec", self.name, "bash", "-lc", cmd], timeout)
        return code, _clip(out)

    def read(self, path: str) -> str:
        return self.exec(f"cat -- {path!r}")[1]

    def _resolve(self, path: str) -> Path:
        path = (path or "").replace("\\", "/")
        if path.startswith("/work/"):
            path = path[len("/work/"):]
        p = (self.host_dir / "repo" / path.lstrip("/")).resolve()
        if (self.host_dir / "repo").resolve() not in p.parents:
            raise ValueError("path escapes repo")
        return p

    def edit(self, path: str, old: str, new: str) -> str:
        """Replace one exact piece of an existing file. The only safe way to change a large file."""
        if not old:
            raise ValueError("edit needs the exact OLD text to replace")
        p = self._resolve(path)
        if not p.is_file():
            raise ValueError(f"{path} does not exist; use write to create a new file")
        raw = p.read_bytes().decode("utf-8")
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        old, new = old.replace("\r\n", "\n"), new.replace("\r\n", "\n")
        n = text.count(old)
        if n == 0:
            first = next((ln.strip() for ln in old.splitlines() if ln.strip()), "")
            hint = f" The first line of OLD is {first!r}; copy the text from the file exactly, including indentation." if first else ""
            raise ValueError("the OLD text was not found in the file." + hint)
        if n > 1:
            raise ValueError(f"the OLD text matches {n} places; include more surrounding lines so it is unique")
        out = text.replace(old, new, 1)
        p.write_bytes((out.replace("\n", "\r\n") if crlf else out).encode("utf-8"))
        return f"edited {path}: replaced {old.count(chr(10)) + 1} line(s) with {new.count(chr(10)) + 1} line(s)"

    def write(self, path: str, content: str) -> None:
        p = self._resolve(path)
        if p.is_file() and "test" not in p.name.lower():
            before = p.read_bytes().decode("utf-8", errors="replace").count("\n")
            if before > 60 and content.count("\n") < 0.7 * before:
                raise ValueError(f"refusing to overwrite the existing {before}-line file {path} with a much shorter one; "
                                 "use the edit action to replace only the lines that must change")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")

    def scratch_files(self) -> list[str]:
        """New files the agent made for its own investigation (debug.py, temp.txt, a root-level test_x.py ...).
        A new file at the repository root is scratch, unless the project has no tests directory and it is a test."""
        has_tests_dir = any((self.host_dir / "repo" / d).is_dir() for d in ("tests", "test"))
        out = []
        for f in self.new_files():
            if "/" in f:
                continue
            is_test = bool(re.search(r"^(test_[^/]*|[^/]*_test)\.py$", f))
            if not (is_test and not has_tests_dir):
                out.append(f)
        return out

    def changed_py_files(self) -> list[str]:
        """Python files the delivered patch changes or adds (scratch files excluded)."""
        repo = str(self.host_dir / "repo")
        _run(["git", "-C", repo, "add", "-N", "."], 60)
        names = set(self._changed(repo, "M")) | set(self._changed(repo, "A"))
        return sorted(n for n in names if n.endswith(".py") and n not in set(self.scratch_files()))

    def diff_zero_context(self) -> str:
        """The delivered patch with no context lines, to know exactly which lines changed."""
        repo = str(self.host_dir / "repo")
        _run(["git", "-C", repo, "add", "-N", "."], 60)
        skip = [f":(exclude){f}" for f in self.scratch_files()]
        return _run(["git", "-C", repo, "diff", "-U0", "--", ".", *skip], 60)[1]

    def diff(self) -> str:
        """The patch to deliver: changes to existing files plus the agent's new tests, without its scratch files."""
        repo = str(self.host_dir / "repo")
        _run(["git", "-C", repo, "add", "-N", "."], 60)
        skip = [f":(exclude){f}" for f in self.scratch_files()]
        return _run(["git", "-C", repo, "diff", "--", ".", *skip], 60)[1]

    def close(self) -> None:
        _run(["docker", "rm", "-f", self.name], 60)
        shutil.rmtree(self.host_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
