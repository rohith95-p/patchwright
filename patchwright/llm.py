"""OpenAI-compatible client wrapper with per-call token, cost and latency accounting."""
import json
import os
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
        self.last_finish = ""
        # Nemotron 3 reasons before it answers. In an agent loop with a large prompt that reasoning can use the whole reply budget
        # and return nothing (finish_reason "length"), at 20-30 seconds per step. PW_THINK=1 turns it back on.
        self.think = os.getenv("PW_THINK", "0") == "1"
        self.cutoffs = 0  # replies that ran out of tokens before answering

    def chat(self, tier: str, messages: list[dict], temperature: float = 0.2, max_tokens: int = 4096) -> str:
        """One completion. A reasoning model can spend the whole budget thinking and return no answer (finish_reason "length");
        that is retried once with a larger budget instead of being treated as a bad reply."""
        model = get_model(tier)
        for widen in (1, 2):
            t0 = time.time()
            resp = None
            for attempt in range(8):
                try:
                    resp = self.client.chat.completions.create(
                        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens * widen, timeout=180,
                        **({} if self.think else {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}})
                    )
                    break
                except (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError):
                    time.sleep(min(60, 3 * 2**attempt))
            if resp is None:
                raise RuntimeError("model endpoint kept failing after retries")
            secs = time.time() - t0
            u = resp.usage
            self.usage.add(tier, getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0, secs)
            self.last_finish = resp.choices[0].finish_reason
            text = resp.choices[0].message.content or ""
            if text or self.last_finish != "length":
                return text
            self.cutoffs += 1
        return text

    def chat_tools(self, tier: str, messages: list[dict], tools: list[dict], temperature: float = 0.2, max_tokens: int = 4096) -> dict:
        """One turn with native tool calling: {"content": text, "calls": [{"id", "name", "args", "raw"}]}. The API returns the calls
        as structured data, so a reply can never be 'unparseable'."""
        model = get_model(tier)
        if self.think:
            max_tokens = max(max_tokens, 8192)  # reasoning is billed as output and must not crowd out the answer
        t0 = time.time()
        resp = None
        for attempt in range(8):
            try:
                resp = self.client.chat.completions.create(
                    model=model, messages=messages, tools=tools, tool_choice="required", temperature=temperature, max_tokens=max_tokens, timeout=180,
                    **({} if self.think else {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}))
                break
            except (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError):
                time.sleep(min(60, 3 * 2**attempt))
        if resp is None:
            raise RuntimeError("model endpoint kept failing after retries")
        u = resp.usage
        self.usage.add(tier, getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0, time.time() - t0)
        msg = resp.choices[0].message
        self.last_finish = resp.choices[0].finish_reason
        calls = []
        for i, tc in enumerate(msg.tool_calls or []):
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
                args = args if isinstance(args, dict) else {}
            except ValueError:
                args = {}
                raw = "{}"
            calls.append({"id": tc.id or f"call_{i}", "name": tc.function.name, "args": args, "raw": raw})
        return {"content": msg.content or "", "calls": calls}

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self.client.models.list().data)
