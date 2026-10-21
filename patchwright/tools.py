"""External tools available to the agent."""
import os

import httpx


def web_search(query: str, max_results: int = 4) -> str:
    key = os.getenv("TAVILY_API_KEY", "")
    if not key:
        return "[search unavailable: TAVILY_API_KEY not set]"
    try:
        r = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": max_results, "search_depth": "basic"},
            timeout=30,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        return f"[search failed: {e}]"
    items = r.json().get("results", [])
    return "\n\n".join(f"{i['title']}\n{i['url']}\n{i['content'][:500]}" for i in items) or "[no results]"
