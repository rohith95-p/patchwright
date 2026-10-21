"""Configuration: provider selection and per-tier model IDs, all from environment (.env)."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

TIERS = ("nano", "super", "ultra")
DEFAULT_URLS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "nebius": "https://api.tokenfactory.nebius.com/v1/",
}


@dataclass(frozen=True)
class Provider:
    name: str
    api_key: str
    base_url: str


def provider_name() -> str:
    return os.getenv("PROVIDER", "nvidia").strip().lower()


def get_provider() -> Provider:
    name = provider_name()
    if name not in DEFAULT_URLS:
        raise SystemExit(f"Unknown PROVIDER '{name}' (use 'nvidia' or 'nebius')")
    key = os.getenv(f"{name.upper()}_API_KEY", "").strip()
    url = os.getenv(f"{name.upper()}_BASE_URL", "").strip()
    if not url.startswith("http"):  # guards against a key pasted on the URL line
        url = DEFAULT_URLS[name]
    if not key:
        raise SystemExit(f"Missing {name.upper()}_API_KEY for provider '{name}'. Fill in .env (see .env.example).")
    return Provider(name, key, url)


def model_env_name(tier: str) -> str:
    """Per-provider model variable first (NEBIUS_SUPER_MODEL), then the shared one (SUPER_MODEL)."""
    specific = f"{provider_name().upper()}_{tier.upper()}_MODEL"
    return specific if os.getenv(specific) else f"{tier.upper()}_MODEL"


def get_model(tier: str) -> str:
    model = os.getenv(model_env_name(tier), "").strip()
    if not model:
        raise SystemExit(f"No model set for tier '{tier}'. Run `python -m patchwright configure`.")
    return model


def _load_prices() -> dict:
    base = {}
    f = Path(__file__).with_name("prices.json")
    if f.exists():
        base = json.loads(f.read_text(encoding="utf-8"))
    out = {}
    for t in TIERS:
        pin, pout = base.get(t, [0.0, 0.0])
        out[t] = (float(os.getenv(f"{t.upper()}_PRICE_IN", pin)), float(os.getenv(f"{t.upper()}_PRICE_OUT", pout)))
    return out


PRICES = _load_prices()
_PRICE_FILE = json.loads(Path(__file__).with_name("prices.json").read_text(encoding="utf-8"))
PRICE_NOTE = _PRICE_FILE.get("_note", "")
PRICES_ARE_ESTIMATES = bool(_PRICE_FILE.get("estimated")) and not any(os.getenv(f"{t.upper()}_PRICE_IN") for t in TIERS)
