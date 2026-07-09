from __future__ import annotations

import httpx

from ..config import get_settings


async def brave_search(query: str, count: int = 10) -> list[dict[str, str]]:
    settings = get_settings()
    if not settings.brave_search_api_key:
        return []
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": settings.brave_search_api_key,
    }
    params = {"q": query, "count": min(count, 20), "safesearch": "moderate"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get("https://api.search.brave.com/res/v1/web/search", headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()
    results = []
    for item in payload.get("web", {}).get("results", []):
        url = item.get("url")
        if url:
            results.append({"url": url, "title": item.get("title", url), "description": item.get("description", "")})
    return results

