"""Build a Patchwright-RW case from a fixing commit: python -m bench.rw_build <owner/repo> <fix_sha> <issue_number> <id>

Needs a clone of the repository (reused from %TEMP%/pw-rw-cache). Writes cases/<id>/{case.json,issue.txt,test.diff,fix_src.diff}.
fix_src.diff (the upstream fix) is kept only so the case can be validated; the agent never sees it.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def git(repo, *a):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(r.stderr[-300:])
    return r.stdout


def main() -> None:
    slug, sha, issue_no, cid = sys.argv[1:5]
    cache = Path(tempfile.gettempdir()) / "pw-rw-cache" / cid
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", f"https://github.com/{slug}.git", str(cache)], check=True)
    sha = git(cache, "rev-parse", sha).strip()
    base = git(cache, "rev-parse", sha + "^").strip()
    files = git(cache, "diff", "--name-only", base, sha).split()
    is_test = lambda f: bool(re.search(r"(^|/)(tests?|testing)/|test_|_test\.py|conftest", f))  # noqa: E731
    tests, src = [f for f in files if is_test(f)], [f for f in files if not is_test(f)]
    r = httpx.get(f"https://api.github.com/repos/{slug}/issues/{issue_no}", headers={"Accept": "application/vnd.github+json"}, timeout=30)
    r.raise_for_status()
    j = r.json()
    body = (j.get("body") or "").replace("\r\n", "\n").strip()
    out = ROOT / "cases" / cid
    out.mkdir(parents=True, exist_ok=True)
    (out / "issue.txt").write_text(f"{j['title']}\n\n{body}\n", encoding="utf-8")
    (out / "test.diff").write_text(git(cache, "diff", base, sha, "--", *tests), encoding="utf-8", newline="\n")
    (out / "fix_src.diff").write_text(git(cache, "diff", base, sha, "--", *src), encoding="utf-8", newline="\n")
    (out / "case.json").write_text(json.dumps({
        "id": cid, "repo": slug, "base_sha": base, "fix_sha": sha, "issue": int(issue_no), "title": j["title"],
        "issue_url": j["html_url"], "test_files": [f for f in tests if f.endswith(".py")], "src_files": src}, indent=1), encoding="utf-8")
    print(cid, "base", base[:8], "src", src, "tests", tests, "issue chars", len(body))


if __name__ == "__main__":
    main()
