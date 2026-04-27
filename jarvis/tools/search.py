# tools/search.py
# Web search for trend research. Hub calls this directly — not a spoke.
# Supports Tavily ($20/month) and SerpAPI ($50/month).
# If SEARCH_API_KEY is not set, returns empty list gracefully.
import os
import logging
import httpx

logger = logging.getLogger(__name__)


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Search the web for current information.
    Returns list of {title, url, snippet} dicts.
    Returns empty list on failure — hub handles gracefully.
    """
    api_key = os.getenv("SEARCH_API_KEY")
    provider = os.getenv("SEARCH_PROVIDER", "tavily").lower()

    if not api_key:
        logger.warning("SEARCH_API_KEY not set. Returning empty search results.")
        return []

    try:
        if provider == "tavily":
            return await _search_tavily(query, max_results, api_key)
        elif provider == "serpapi":
            return await _search_serpapi(query, max_results, api_key)
        else:
            logger.error(f"Unknown SEARCH_PROVIDER: {provider}. Use 'tavily' or 'serpapi'.")
            return []
    except Exception as e:
        logger.error(f"web_search failed for '{query}': {e}")
        return []


async def _search_tavily(query: str, max_results: int, api_key: str) -> list[dict]:
    """Tavily search — better for AI agents, $20/month."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
            })
        logger.debug(f"Tavily returned {len(results)} results for: {query}")
        return results


async def _search_serpapi(query: str, max_results: int, api_key: str) -> list[dict]:
    """SerpAPI search — $50/month, Google results."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            "https://serpapi.com/search",
            params={
                "api_key": api_key,
                "q": query,
                "num": max_results,
                "engine": "google",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("organic_results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            })
        logger.debug(f"SerpAPI returned {len(results)} results for: {query}")
        return results
