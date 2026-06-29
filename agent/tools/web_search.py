import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def web_search(
    query: str,
    num_results: int = 5,
    include_contents: bool = True,
) -> dict[str, Any]:
    """Search the web using SearXNG to find relevant information.

    Use this tool when you need to find documentation, code examples, GitHub repos,
    news, or research papers to help complete a task.

    Args:
        query: The search query
        num_results: Number of results to return (default: 5)
        include_contents: Whether to include full page contents (default: True)

    Returns:
        Dictionary containing:
        - success: Whether the search succeeded
        - results: Search results from SearXNG
        - error: Error message if something failed
    """
    base_url = os.environ.get("SEARXNG_BASE_URL", "http://localhost:8888")

    async def _search() -> dict[str, Any]:
        params = {
            "q": query,
            "format": "json",
            "language": "en",
            "safesearch": "0",
            "pageno": "1",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{base_url}/search", params=params)
            response.raise_for_status()
            data = response.json()

        results = data.get("results", [])[:num_results]

        formatted = []
        for r in results:
            entry: dict[str, Any] = {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            if include_contents and r.get("content"):
                entry["content"] = r.get("content", "")
            formatted.append(entry)

        return {"success": True, "results": str(formatted), "error": None}

    try:
        return await _search()
    except Exception as e:
        logger.exception("web_search failed")
        return {"success": False, "results": None, "error": f"{type(e).__name__}: {e}"}