import asyncio
import logging
from typing import Any

from openswe.config import ENV
from openswe.tools.mcp_exposure import expose_mcp
from openswe.tools.sandbox_output import chunk_output_as_jsonl, write_sandbox_output

logger = logging.getLogger(__name__)

WEB_SEARCH_MAX_INLINE_CHARS = 100_000


@expose_mcp()
async def web_search(
    query: str,
    num_results: int = 5,
    include_contents: bool = True,
) -> dict[str, Any]:
    """Implement the `web_search` tool."""
    api_key = ENV.EXA_API_KEY.optional()
    if not api_key:
        logger.warning("exa_api_key_missing")
        return {
            "success": False,
            "error": "EXA_API_KEY is not configured. Please add it to your environment variables.",
        }

    async def _search() -> dict[str, Any]:
        from exa_py import Exa  # deferred: heavy import

        client = Exa(api_key=api_key)
        # search() returns truncated page text unless contents is set explicitly.
        result = await asyncio.to_thread(
            client.search,
            query,
            contents={"text": True} if include_contents else False,
            num_results=num_results,
            type="auto",
        )
        results = str(result)
        try:
            results_path = await write_sandbox_output(
                "web-search", chunk_output_as_jsonl(results), "jsonl"
            )
        except Exception:
            logger.info("Web search sandbox unavailable; returning bounded inline results")
            return {
                "success": True,
                "results_path": None,
                "results": _bounded_inline_results(results),
                "result_chars": len(results),
                "error": None,
            }
        return {
            "success": True,
            "results_path": results_path,
            "results": None,
            "result_chars": len(results),
            "error": None,
        }

    try:
        return await _search()
    except Exception as e:
        logger.exception("web_search failed")
        return {
            "success": False,
            "results_path": None,
            "results": None,
            "result_chars": 0,
            "error": f"{type(e).__name__}: {e}",
        }


def _bounded_inline_results(results: str) -> str:
    if len(results) <= WEB_SEARCH_MAX_INLINE_CHARS:
        return results
    return (
        results[:WEB_SEARCH_MAX_INLINE_CHARS]
        + "\n... [results truncated: "
        + f"{WEB_SEARCH_MAX_INLINE_CHARS}/{len(results)} chars]\n"
    )
