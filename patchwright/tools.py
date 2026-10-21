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
# Bug research belongs on developer sources; without this a query can match unrelated pages (a pest-control page, in one real run).
DEFAULT_DOMAINS = ["github.com", "stackoverflow.com", "docs.python.org", "python.org", "pypi.org", "readthedocs.io", "readthedocs.org",
                   "discuss.python.org", "realpython.com", "peps.python.org"]


def _domains() -> list[str]:
    raw = os.getenv("TAVILY_DOMAINS")
    if raw is None:
        return DEFAULT_DOMAINS
    return [d.strip() for d in raw.split(",") if d.strip()]  # an empty TAVILY_DOMAINS searches the whole web


def _call(key: str, query: str, max_results: int, domains: list[str]) -> list[dict]:
    r = httpx.post(API, json={"api_key": key, "query": query[:380], "max_results": max_results, "search_depth": "basic",
                              **({"include_domains": domains} if domains else {})}, timeout=30)
    r.raise_for_status()
    return [{"title": i.get("title", ""), "url": i.get("url", ""), "content": (i.get("content") or "")[:320]}
            for i in r.json().get("results", [])]


def search(query: str, max_results: int = 3) -> list[dict]:
    """Tavily results as [{title, url, content}], or [] when the key is missing or the call fails.
    Developer sources first; if that finds nothing, one search of the whole web."""
    key = os.getenv("TAVILY_API_KEY", "")
    if not key or not query.strip():
        return []
    last = ""
    for domains in (_domains(), []):
        found: list[dict] = []
        for attempt in range(3):  # a transient failure or brief rate limit should not silently disable the feature
            try:
                found = _call(key, query, max_results, domains)
                break
            except (httpx.HTTPError, ValueError) as e:
                last = f"{type(e).__name__}: {e}"[:200]
                time.sleep(1.5 * (attempt + 1))
        if found:
            search.last_error = ""
            return found
        if not domains:
            break
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
# (pattern, query template, index of the group that names the module, or None)
_RESEARCH = [
    (re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'"), "python ModuleNotFoundError No module named '{0}' which pip package", 0),
    (re.compile(r"ImportError: cannot import name '(\w+)' from '([\w.]+)'"), "python ImportError cannot import name '{0}' from '{1}'", 1),
    (re.compile(r"AttributeError: module '([\w.]+)' has no attribute '(\w+)'"), "python AttributeError module '{0}' has no attribute '{1}' removed or renamed", 0),
    (re.compile(r"TypeError: .{0,80}got an unexpected keyword argument '(\w+)'"), "python TypeError got an unexpected keyword argument '{0}'", None),
    (re.compile(r"(DeprecationWarning|FutureWarning): (.{10,160})"), "python {0} {1}", None),
]


def research_query(output: str, own: frozenset | set = frozenset()) -> str | None:
    """A Tavily query for a library or version style error in command output, or None if the error is the agent's own.
    `own` holds the project's own module names: a failing import of those is the agent's doing, not a library mystery."""
    for pattern, template, module_group in _RESEARCH:
        m = pattern.search(output)
        if not m:
            continue
        if module_group is not None and m.group(module_group + 1).split(".")[0] in own:
            continue
        return template.format(*m.groups()).strip()[:300]
    return None
