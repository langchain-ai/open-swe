import asyncio
import logging
import os
from typing import Any

from exa_py import Exa

logger = logging.getLogger(__name__)


def _truncate_result_texts(result: Any, max_chars: int) -> None:
    """Head-truncate each Exa result's ``text`` field in place."""
    items = getattr(result, "results", None)
    if not isinstance(items, list):
        return
    for item in items:
        text = getattr(item, "text", None)
        if isinstance(text, str) and len(text) > max_chars:
            try:
                item.text = text[:max_chars]
            except Exception:
                pass


async def web_search(
    query: str,
    num_results: int = 5,
    include_contents: bool = False,
    max_chars_per_result: int = 8000,
) -> dict[str, Any]:
    """Search the web using Exa to find relevant information.

    Use this tool when you need to find documentation, code examples, GitHub repos,
    news, or research papers to help complete a task.

    Args:
        query: The search query
        num_results: Number of results to return (default: 5)
        include_contents: Whether to include full page contents (default: False).
            Snippet-only mode by default; opt in with ``True`` to fetch full text,
            which is then truncated per-result to ``max_chars_per_result``.
        max_chars_per_result: When ``include_contents=True``, head-truncate each
            result's ``text`` field to this many characters (default: 8000).

    Returns:
        Dictionary containing:
        - success: Whether the search succeeded
        - results: Search results from Exa
        - error: Error message if something failed
    """
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        logger.warning("exa_api_key_missing")
        return {
            "success": False,
            "error": "EXA_API_KEY is not configured. Please add it to your environment variables.",
        }

    async def _search() -> dict[str, Any]:
        client = Exa(api_key=api_key)
        if include_contents:
            result = await asyncio.to_thread(
                client.search_and_contents,
                query,
                text=True,
                num_results=num_results,
                type="auto",
            )
            _truncate_result_texts(result, max_chars_per_result)
        else:
            result = await asyncio.to_thread(
                client.search,
                query,
                num_results=num_results,
                type="auto",
            )
        return {"success": True, "results": str(result), "error": None}

    try:
        return await _search()
    except Exception as e:
        logger.exception("web_search failed")
        return {"success": False, "results": None, "error": f"{type(e).__name__}: {e}"}
