"""Proof card and optional GitHub PR creation.

proof_card() is tested locally. open_pr() needs GITHUB_TOKEN and a repo you can push to; it is
only used when you pass --pr, and it has not been exercised against a live repository yet.
"""
import os
import subprocess
import tempfile

import httpx

from .agent import Result


def proof_card(issue: str, res: Result, usage: dict, models: dict) -> str:
    cost = sum(t["cost_usd"] for t in usage.values())
    tokens = sum(t["tokens"] for t in usage.values())
    return f"""## Patchwright fix (verified)

**Issue:** {issue.strip()[:500]}

**Root cause and fix:** {res.summary}

### Proof
- A new test reproduces the bug: it **fails with the source change reverted** and **passes with it** (checked by the harness, not by the model).
- The project's existing test suite passes. No existing test files were modified.

### Run details
- Solved at tier: `{res.tier_reached}` in {res.steps} steps, {res.seconds:.0f}s
- Models: {', '.join(f'{k}={v}' for k, v in models.items())}
- Tokens: {tokens:,}, cost: ${cost:.4f}

<details><summary>Diff</summary>

```diff
{res.diff}
```
</details>
"""


def open_pr(repo_url: str, branch: str, title: str, body: str, diff: str) -> str:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("GITHUB_TOKEN is not set.")
    slug = repo_url.removesuffix(".git").split("github.com/")[-1]
    work = tempfile.mkdtemp(prefix="pw-pr-")
    auth_url = f"https://x-access-token:{token}@github.com/{slug}.git"
    run = lambda *a: subprocess.run(a, cwd=work, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")  # noqa: E731
    run("git", "clone", "--quiet", auth_url, "repo")
    repo = os.path.join(work, "repo")
    patch = os.path.join(work, "fix.patch")
    open(patch, "w", encoding="utf-8", newline="").write(diff)
    git = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")  # noqa: E731
    git("checkout", "-q", "-b", branch)
    git("apply", "--whitespace=nowarn", patch)
    git("add", "-A")
    git("-c", "user.name=patchwright", "-c", "user.email=patchwright@users.noreply.github.com", "commit", "-qm", title)
    git("push", "-q", "origin", branch)
    base = git("rev-parse", "--abbrev-ref", "origin/HEAD").stdout.strip().split("/")[-1]
    r = httpx.post(
        f"https://api.github.com/repos/{slug}/pulls",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"title": title, "head": branch, "base": base, "body": body},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["html_url"]
