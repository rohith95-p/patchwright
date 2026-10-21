"""`doctor` (readiness check) and `configure` (auto-pick Nemotron models for the active provider)."""
import os
import re
import shutil
import subprocess
import time

from openai import OpenAI

from .config import ENV_PATH, TIERS, get_model, get_provider, provider_name

EXCLUDE = re.compile(r"embed|safety|guard|parse|reward|vl\b|-vl-|omni-?vision|retriev|rerank|content", re.I)


def pick_models(ids: list[str]) -> dict[str, str]:
    """Choose one Nemotron model per tier from a provider's model list (newest/largest wins)."""
    cands = [i for i in ids if re.search(r"nemotron", i, re.I) and not EXCLUDE.search(i)]

    def score(i: str) -> tuple:
        nums = [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)", i)]
        gen = next((n for n in nums if n < 10), 0)  # generation number like 3 or 3.5
        size = max((n for n in nums if n >= 10), default=0)
        return (gen, size)

    picks = {}
    for tier, pats in (("nano", [r"nano", r"lightning|mini"]), ("super", [r"super"]), ("ultra", [r"ultra"])):
        for pat in pats:
            pool = [i for i in cands if re.search(pat, i, re.I)]
            pool = [i for i in pool if not re.search(r"llama", i, re.I)] or pool  # prefer native Nemotron 3.x
            if pool:
                picks[tier] = max(pool, key=score)
                break
    return picks


def _write_env(updates: dict[str, str]) -> None:
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    for k, v in updates.items():
        if re.search(rf"^{k}=", text, re.M):
            text = re.sub(rf"^{k}=.*$", lambda _: f"{k}={v}", text, count=1, flags=re.M)
        else:
            text += f"\n{k}={v}"
    ENV_PATH.write_text(text, encoding="utf-8")


def configure() -> None:
    prov = get_provider()
    client = OpenAI(api_key=prov.api_key, base_url=prov.base_url)
    ids = sorted(m.id for m in client.models.list().data)
    print(f"{prov.name}: {len(ids)} models visible")
    picks = pick_models(ids)
    prefix = prov.name.upper()
    updates = {}
    for tier in TIERS:
        if tier in picks:
            print(f"  {tier:<6} -> {picks[tier]}")
            updates[f"{prefix}_{tier.upper()}_MODEL"] = picks[tier]
        else:
            print(f"  {tier:<6} -> NOT FOUND (set {prefix}_{tier.upper()}_MODEL by hand; see `models`)")
    if updates:
        _write_env(updates)
        print(f"Saved to {ENV_PATH.name}. Run `python -m patchwright doctor` to verify each model responds.")


def _check(label: str, fn) -> bool:
    t0 = time.time()
    try:
        detail = fn()
        print(f"  [ OK ] {label:<28} {detail or ''} ({time.time() - t0:.1f}s)")
        return True
    except SystemExit as e:
        print(f"  [FAIL] {label:<28} {e}")
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] {label:<28} {str(e)[:140]}")
    return False


def doctor() -> bool:
    print(f"Provider: {provider_name()}")
    results = []

    def docker():
        if not shutil.which("docker"):
            raise RuntimeError("docker not found")
        out = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=30)
        if out.returncode:
            raise RuntimeError("Docker daemon not running")
        return f"server {out.stdout.strip()}"

    results.append(_check("Docker", docker))
    prov_holder = {}

    def key():
        prov_holder["p"] = get_provider()
        return f"{prov_holder['p'].base_url}"

    results.append(_check("API key + endpoint", key))
    if "p" in prov_holder:
        p = prov_holder["p"]
        client = OpenAI(api_key=p.api_key, base_url=p.base_url)
        results.append(_check("List models", lambda: f"{len(client.models.list().data)} models"))
        for tier in TIERS:
            def call(tier=tier):
                m = get_model(tier)
                for attempt in range(5):
                    try:
                        r = client.chat.completions.create(model=m, messages=[{"role": "user", "content": "Reply with the word OK."}],
                                                           max_tokens=200, timeout=90)
                        break
                    except Exception as e:  # noqa: BLE001
                        if "429" not in str(e) and "503" not in str(e) or attempt == 4:
                            raise
                        time.sleep(5 * (attempt + 1))
                return f"{m} -> {(r.choices[0].message.content or '').strip()[:20]!r}"
            results.append(_check(f"Model tier '{tier}'", call))

    def tavily():
        if not os.getenv("TAVILY_API_KEY"):
            raise RuntimeError("TAVILY_API_KEY not set (search tool will be disabled)")
        from .tools import web_search
        out = web_search("python list sort stable", 1)
        if out.startswith("["):
            raise RuntimeError(out)
        return "search works"

    _check("Tavily search", tavily)  # optional: not counted as fatal
    ok = all(results)
    print("\nREADY" if ok else "\nNOT READY: fix the FAIL lines above.")
    return ok
