import json
import logging
from typing import Any

import httpx2

from agent.tools.errors import ToolError
from agent.tools.sandbox_output import chunk_output_as_jsonl, write_sandbox_output
from agent.tools.sandbox_preference import replaced_by_curl
from agent.utils.url_safety import (
    request_with_safe_redirects as _request_with_safe_redirects,
)

logger = logging.getLogger(__name__)

HTTP_REQUEST_MAX_INLINE_CHARS = 100_000


@replaced_by_curl
async def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: str | dict | None = None,
    params: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Implement the `http_request` tool."""
    try:
        kwargs: dict[str, Any] = {}

        if headers:
            kwargs["headers"] = headers
        if params:
            kwargs["params"] = params
        if data:
            if isinstance(data, dict):
                kwargs["json"] = data
            else:
                kwargs["content"] = data

        async with httpx2.AsyncClient(timeout=timeout) as client:
            response = await _request_with_safe_redirects(
                client,
                method,
                url,
                **kwargs,
            )

        try:
            content = response.json()
        except ValueError:
            content = response.text

        result = {
            "success": response.status_code < 400,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content": content,
            "url": str(response.url),
        }
        result = await _offload_large_response(result)
        if response.status_code >= 400:
            raise ToolError(f"HTTP {response.status_code}", details=result)
        return result

    except httpx2.TimeoutException as exc:
        raise ToolError(
            f"Request timed out after {timeout} seconds", details={"status_code": 0, "url": url}
        ) from exc
    except httpx2.HTTPError as e:
        raise ToolError(f"Request error: {e!s}", details={"status_code": 0, "url": url}) from e


async def _offload_large_response(result: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if len(serialized) <= HTTP_REQUEST_MAX_INLINE_CHARS:
        return result

    try:
        response_path = await write_sandbox_output(
            "http-response", chunk_output_as_jsonl(serialized), "jsonl"
        )
    except Exception as exc:
        logger.exception("Failed to save oversized HTTP response to sandbox")
        raise ToolError(
            "Response exceeded the inline limit and could not be saved to the sandbox",
            details={
                "status_code": result["status_code"],
                "url": result["url"],
                "response_chars": len(serialized),
            },
        ) from exc

    return {
        "success": result["success"],
        "status_code": result["status_code"],
        "url": result["url"],
        "response_path": response_path,
        "response_chars": len(serialized),
    }
