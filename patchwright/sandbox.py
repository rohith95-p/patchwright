"""Docker sandbox: the repo lives in a throwaway container; the agent never touches the host."""
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

IMAGE = "python:3.11-slim"
MAX_OUT = 8000


def _run(args: list[str], timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired:
        return 124, f"[timeout after {timeout}s]"


def _clip(s: str) -> str:
    return s if len(s) <= MAX_OUT else s[: MAX_OUT // 2] + "\n...[clipped]...\n" + s[-MAX_OUT // 2 :]


class Sandbox:
    def __init__(self, repo_url: str, ref: str | None = None) -> None:
        self.name = f"patchwright-{uuid.uuid4().hex[:8]}"
        self.host_dir = Path(tempfile.mkdtemp(prefix="pw-"))
        code, out = _run(["git", "clone", "--quiet", repo_url, str(self.host_dir / "repo")], 300)
        if code:
            raise RuntimeError(f"git clone failed: {out}")
        if ref:
            _run(["git", "-C", str(self.host_dir / "repo"), "checkout", "--quiet", ref], 60)
        code, out = _run(
            ["docker", "run", "-d", "--name", self.name, "--memory", "2g", "--cpus", "2",
             "-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{self.host_dir / 'repo'}:/work", "-w", "/work", IMAGE, "sleep", "infinity"],
            120,
        )
        if code:
            raise RuntimeError(f"docker run failed: {out}")

    def setup(self) -> None:
        self.exec("pip install -q pytest 2>&1 | tail -1", timeout=240)

    def prove_fails_without_fix(self, repro_cmd: str) -> tuple[bool, str]:
        """Revert changes to tracked source files (keeping new test files), run the repro test, restore the fix."""
        repo = str(self.host_dir / "repo")
        names = [n for n in _run(["git", "-C", repo, "diff", "--name-only"], 60)[1].splitlines()
                 if n and not n.startswith("warning")]
        if not names:
            return False, "No change to existing source files was found; a fix must modify the code."
        edited_tests = [n for n in names if "test" in Path(n).name.lower()]
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

    def reset(self) -> None:
        """Restore the repo to its original commit (used when escalating to a stronger model)."""
        repo = str(self.host_dir / "repo")
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

    def write(self, path: str, content: str) -> None:
        path = path.replace("\\", "/")
        if path.startswith("/work/"):
            path = path[len("/work/"):]
        p = (self.host_dir / "repo" / path.lstrip("/")).resolve()
        if (self.host_dir / "repo").resolve() not in p.parents:
            raise ValueError("path escapes repo")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")

    def diff(self) -> str:
        repo = str(self.host_dir / "repo")
        _run(["git", "-C", repo, "add", "-N", "."], 60)
        return _run(["git", "-C", repo, "diff"], 60)[1]

    def close(self) -> None:
        _run(["docker", "rm", "-f", self.name], 60)
        shutil.rmtree(self.host_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
