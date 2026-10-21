"""OpenAI-compatible client wrapper with per-call token, cost and latency accounting."""
import time
from dataclasses import dataclass, field

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from .config import PRICES, get_model, get_provider


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    by_tier: dict = field(default_factory=dict)

    def add(self, tier: str, p: int, c: int, secs: float) -> None:
        pin, pout = PRICES[tier]
        cost = (p * pin + c * pout) / 1_000_000
        self.calls += 1
        self.prompt_tokens += p
        self.completion_tokens += c
        self.cost_usd += cost
        self.seconds += secs
        t = self.by_tier.setdefault(tier, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
        t["calls"] += 1
        t["tokens"] += p + c
        t["cost_usd"] += cost


class LLM:
    def __init__(self) -> None:
        prov = get_provider()
        self.provider = prov.name
        self.client = OpenAI(api_key=prov.api_key, base_url=prov.base_url)
        self.usage = Usage()

    def chat(self, tier: str, messages: list[dict], temperature: float = 0.2, max_tokens: int = 4096) -> str:
        model = get_model(tier)
        t0 = time.time()
        resp = None
        for attempt in range(8):
            try:
                resp = self.client.chat.completions.create(
                    model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, timeout=180
                )
                break
            except (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError):
                time.sleep(min(60, 3 * 2**attempt))
        if resp is None:
            raise RuntimeError("model endpoint kept failing after retries")
        secs = time.time() - t0
        u = resp.usage
        self.usage.add(tier, getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0, secs)
        return resp.choices[0].message.content or ""

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self.client.models.list().data)
