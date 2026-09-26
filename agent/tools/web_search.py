import asyncio
import logging
import re
from typing import Any

from agent.config import ENV
from agent.tools.report_platform_issue import report_platform_issue
from agent.tools.sandbox_output import chunk_output_as_jsonl, write_sandbox_output

logger = logging.getLogger(__name__)

WEB_SEARCH_MAX_INLINE_CHARS = 100_000
_provider_unavailable_reported = False
_STATUS_CODE_PATTERN = re.compile(r"status code (\d+)", re.IGNORECASE)
_SENSITIVE_PROVIDER_ERROR_PATTERN = re.compile(
    r"billing|credits?|upsell|upgrade|dashboard|credentials?|api key|response body",
    re.IGNORECASE,
)


async def web_search(
    query: str,
    num_results: int = 5,
    include_contents: bool = True,
) -> dict[str, Any]:
    """Implement the `web_search` tool."""
    api_key = ENV.EXA_API_KEY.optional()
    if not api_key:
        logger.warning("exa_api_key_missing")
        return _failure_response(
            "search_unavailable",
            False,
            "EXA_API_KEY is not configured. Please add it to your environment variables.",
        )

    async def _search() -> dict[str, Any]:
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
                "status": "success",
                "retryable": False,
                "results_path": None,
                "results": _bounded_inline_results(results),
                "result_chars": len(results),
                "error": None,
            }
        return {
            "success": True,
            "status": "success",
            "retryable": False,
            "results_path": results_path,
            "results": None,
            "result_chars": len(results),
            "error": None,
        }

    try:
        return await _search()
    except Exception as e:
        logger.exception("web_search failed")
        status_code = _extract_http_status(e)
        if status_code in {401, 402, 403}:
            await _report_provider_unavailable(status_code)
            return _failure_response(
                "search_unavailable",
                False,
                "Web search is unavailable for this deployment "
                f"(search provider returned {status_code}). Do not retry this tool in this turn; "
                "if the answer depends on web evidence, say the web could not be consulted and "
                "fall back to fetch_url on a known URL.",
            )
        if status_code == 429 or status_code is not None and status_code >= 500 or _is_timeout(e):
            return _failure_response(
                "search_error",
                True,
                "Web search temporarily failed. The tool may be retried.",
            )
        return _failure_response("search_error", False, _sanitized_exception_error(e))


def _failure_response(status: str, retryable: bool, error: str) -> dict[str, Any]:
    return {
        "success": False,
        "status": status,
        "retryable": retryable,
        "results_path": None,
        "results": None,
        "result_chars": 0,
        "error": error,
    }


def _extract_http_status(error: Exception) -> int | None:
    for attribute in ("status_code", "status"):
        value = getattr(error, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    match = _STATUS_CODE_PATTERN.search(str(error))
    return int(match.group(1)) if match else None


def _is_timeout(error: Exception) -> bool:
    return isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()


def _sanitized_exception_error(error: Exception) -> str:
    message = str(error).strip()
    if not message or _SENSITIVE_PROVIDER_ERROR_PATTERN.search(message):
        message = "provider error details redacted"
    return f"{type(error).__name__}: {message}"


async def _report_provider_unavailable(status_code: int) -> None:
    global _provider_unavailable_reported
    if _provider_unavailable_reported:
        return
    _provider_unavailable_reported = True
    logger.error("web_search_provider_unavailable", extra={"status_code": status_code})
    try:
        await report_platform_issue(
            problem_description=f"Web search provider returned HTTP {status_code}",
            keywords=["web_search", "search_provider_unavailable"],
        )
    except Exception:
        logger.warning("web_search_provider_issue_report_failed", exc_info=True)


def _bounded_inline_results(results: str) -> str:
    if len(results) <= WEB_SEARCH_MAX_INLINE_CHARS:
        return results
    return (
        results[:WEB_SEARCH_MAX_INLINE_CHARS]
        + "\n... [results truncated: "
        + f"{WEB_SEARCH_MAX_INLINE_CHARS}/{len(results)} chars]\n"
    )
