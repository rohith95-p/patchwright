"""CLI.

  python -m patchwright doctor                    check Docker, keys, endpoint and every model tier
  python -m patchwright configure                 auto-pick Nano/Super/Ultra model IDs for the active provider
  python -m patchwright models                    list models your key can see
  python -m patchwright fix <repo_url> <issue>    fix a bug (issue = text or a file path)
"""
import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "doctor":
        from .doctor import doctor
        raise SystemExit(0 if doctor() else 1)
    if cmd == "configure":
        from .doctor import configure
        return configure()
    from .llm import LLM
    llm = LLM()
    if cmd == "models":
        print("\n".join(llm.list_models()))
    elif cmd == "fix" and len(sys.argv) >= 4:
        from .agent import run_agent
        issue = sys.argv[3]
        if Path(issue).is_file():
            issue = Path(issue).read_text(encoding="utf-8")
        Path("runs").mkdir(exist_ok=True)
        live = Path("runs/live.jsonl")
        live.write_text("", encoding="utf-8")

        def on_event(e: dict) -> None:
            print(json.dumps(e, ensure_ascii=False)[:300])
            with live.open("a", encoding="utf-8") as f:
                f.write(json.dumps(e) + chr(10))

        res = run_agent(llm, sys.argv[2], issue, on_event=on_event)
        print("\n=== RESULT ===")
        print(f"solved={res.solved} tier={res.tier_reached} steps={res.steps} time={res.seconds:.0f}s")
        print(f"cost=${llm.usage.cost_usd:.4f} tokens={llm.usage.prompt_tokens + llm.usage.completion_tokens}")
        print(res.summary)
        print(res.diff)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
