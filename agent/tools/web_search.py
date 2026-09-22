import asyncio
import json
import logging
from functools import partial
from typing import Any, Literal

from langchain_mcp_adapters.sessions import StreamableHttpConnection, create_session
from mcp.types import CallToolResult, PaginatedRequestParams, TextContent

from agent.config import ENV
from agent.mcp.transport import mcp_http_client
from agent.tools.sandbox_output import chunk_output_as_jsonl, write_sandbox_output

logger = logging.getLogger(__name__)

WEB_SEARCH_MAX_INLINE_CHARS = 100_000
PARALLEL_SEARCH_MCP_URL = "https://search.parallel.ai/mcp"


def _mcp_payload(result: CallToolResult) -> dict[str, object]:
    if result.isError:
        message = next(
            (item.text for item in result.content if isinstance(item, TextContent)),
            "MCP tool failed",
        )
        raise RuntimeError(message[:1000])
    payload: object = result.structuredContent
    if payload is None:
        text = next(
            (item.text for item in result.content if isinstance(item, TextContent)),
            None,
        )
        payload = json.loads(text) if text is not None else None
    if not isinstance(payload, dict):
        raise ValueError("MCP tool returned an invalid result")
    return payload


async def _parallel_search(query: str, num_results: int, include_contents: bool) -> str:
    if num_results < 1:
        raise ValueError("num_results must be positive")
    connection: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": PARALLEL_SEARCH_MCP_URL,
        "headers": {"User-Agent": "open-swe"},
        "timeout": 30,
        "sse_read_timeout": 30,
        "httpx_client_factory": partial(mcp_http_client, PARALLEL_SEARCH_MCP_URL),
    }
    async with create_session(connection) as session:
        await session.initialize()
        page = await session.list_tools()
        available = {tool.name for tool in page.tools}
        while page.nextCursor:
            page = await session.list_tools(params=PaginatedRequestParams(cursor=page.nextCursor))
            available.update(tool.name for tool in page.tools)
        required = {"web_search", "web_fetch"} if include_contents else {"web_search"}
        if not required <= available:
            raise ValueError("Parallel Search MCP is missing required tools")
        search = _mcp_payload(
            await session.call_tool("web_search", {"objective": query, "search_queries": [query]})
        )
        raw_results = search.get("results")
        if not isinstance(raw_results, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("url"), str)
            for item in raw_results
        ):
            raise ValueError("Parallel search returned invalid results")
        results = raw_results[:num_results]
        output: dict[str, object] = {
            "provider": "parallel",
            "results": results,
            "warnings": search.get("warnings"),
        }
        if include_contents and results:
            urls = [item["url"] for item in results]
            fetched_results: list[object] = []
            fetch_errors: list[object] = []
            for offset in range(0, len(urls), 20):
                fetched = _mcp_payload(
                    await session.call_tool(
                        "web_fetch",
                        {
                            "urls": urls[offset : offset + 20],
                            "objective": query[:200],
                            "full_content": True,
                            **(
                                {"session_id": search["session_id"]}
                                if isinstance(search.get("session_id"), str)
                                else {}
                            ),
                        },
                    )
                )
                fetched_items = fetched.get("results")
                if not isinstance(fetched_items, list):
                    raise ValueError("Parallel fetch returned invalid results")
                fetched_results.extend(fetched_items)
                errors = fetched.get("errors")
                if isinstance(errors, list):
                    fetch_errors.extend(errors)
            contents = {
                item["url"]: item["full_content"]
                for item in fetched_results
                if isinstance(item, dict)
                and isinstance(item.get("url"), str)
                and isinstance(item.get("full_content"), str)
            }
            output["results"] = [
                {
                    **item,
                    **({"full_content": contents[item["url"]]} if item["url"] in contents else {}),
                }
                for item in results
            ]
            output["fetch_errors"] = fetch_errors
    return json.dumps(output, ensure_ascii=False)


async def web_search(
    query: str,
    num_results: int = 5,
    include_contents: bool = True,
    provider: Literal["exa", "parallel"] = "exa",
) -> dict[str, Any]:
    """Implement the `web_search` tool."""
    api_key = ENV.EXA_API_KEY.optional() if provider == "exa" else None
    if provider == "exa" and not api_key:
        logger.warning("exa_api_key_missing")
        return {
            "success": False,
            "error": "EXA_API_KEY is not configured. Please add it to your environment variables.",
        }

    async def _search() -> dict[str, Any]:
        if provider == "parallel":
            results = await _parallel_search(query, num_results, include_contents)
        else:
            from exa_py import Exa  # deferred: heavy import

            client = Exa(api_key=api_key)
            if include_contents:
                result = await asyncio.to_thread(
                    client.search_and_contents,
                    query,
                    text=True,
                    num_results=num_results,
                    type="auto",
                )
            else:
                result = await asyncio.to_thread(
                    client.search,
                    query,
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
