"""External tools: Tavily web search, used two ways.

1. The model can ask for it (`search` action).
2. The harness uses it on its own where it clearly helps, so the agent benefits even if the model never asks:
   - when a command fails with an error that points at a library or version problem (not the agent's own assertions)
   - when the task is a real remote repository, to find related discussion of the reported symptom
Every query and its sources are returned so the interface and the pull-request description can show them.
"""
import os
import re
import time

import httpx

from .config import ENV_PATH  # importing config loads .env, so TAVILY_API_KEY is always available

ENV_FILE = ENV_PATH
API = "https://api.tavily.com/search"


def search(query: str, max_results: int = 3) -> list[dict]:
    """Tavily results as [{title, url, content}], or [] when the key is missing or the call fails."""
    key = os.getenv("TAVILY_API_KEY", "")
    if not key or not query.strip():
        return []
    last = ""
    for attempt in range(3):  # a transient failure or brief rate limit should not silently disable the feature
        try:
            r = httpx.post(API, json={"api_key": key, "query": query[:380], "max_results": max_results, "search_depth": "basic"}, timeout=30)
            r.raise_for_status()
            search.last_error = ""
            return [{"title": i.get("title", ""), "url": i.get("url", ""), "content": (i.get("content") or "")[:320]}
                    for i in r.json().get("results", [])]
        except (httpx.HTTPError, ValueError) as e:
            last = f"{type(e).__name__}: {e}"[:200]
            time.sleep(1.5 * (attempt + 1))
    search.last_error = last
    return []


search.last_error = ""


def format_results(results: list[dict]) -> str:
    return "\n\n".join(f"{i['title']}\n{i['url']}\n{i['content']}" for i in results) or "[no results]"


def web_search(query: str, max_results: int = 4) -> str:
    """The `search` action: results as text for the model."""
    if not os.getenv("TAVILY_API_KEY", ""):
        return "[search unavailable: TAVILY_API_KEY not set]"
    return format_results(search(query, max_results))


# Errors that usually mean "a library or its version", where the web helps. Errors in the agent's own code or
# assertions (AssertionError, KeyError, ZeroDivisionError ...) are deliberately not researched.
_RESEARCH = [
    (re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'"), "python ModuleNotFoundError No module named '{0}' which pip package"),
    (re.compile(r"ImportError: cannot import name '(\w+)' from '([\w.]+)'"), "python ImportError cannot import name '{0}' from '{1}'"),
    (re.compile(r"AttributeError: module '([\w.]+)' has no attribute '(\w+)'"), "python AttributeError module '{0}' has no attribute '{1}' removed or renamed"),
    (re.compile(r"TypeError: .{0,80}got an unexpected keyword argument '(\w+)'"), "python TypeError got an unexpected keyword argument '{0}'"),
    (re.compile(r"(DeprecationWarning|FutureWarning): (.{10,160})"), "python {0} {1}"),
]


def research_query(output: str) -> str | None:
    """A Tavily query for a library or version style error in command output, or None if the error is the agent's own."""
    for pattern, template in _RESEARCH:
        m = pattern.search(output)
        if m:
            return template.format(*m.groups()).strip()[:300]
    return None
