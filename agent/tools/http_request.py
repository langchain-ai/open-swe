from typing import Any

import httpx

from ..utils.url_safety import (
    request_with_safe_redirects as _request_with_safe_redirects,
)


async def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: str | dict | None = None,
    params: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Make HTTP requests to APIs and web services.

    Do not use this tool for GitHub API calls. Use `GH_TOKEN=dummy gh` in the
    sandbox so GitHub authentication is handled by the sandbox proxy.

    Args:
        url: Target URL
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        headers: HTTP headers to include
        data: Request body data (string or dict)
        params: URL query parameters
        timeout: Request timeout in seconds

    Returns:
        Dictionary with response data including status, headers, and content
    """
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

        async with httpx.AsyncClient(timeout=timeout) as client:
            response, blocked = await _request_with_safe_redirects(
                client,
                method,
                url,
                **kwargs,
            )
        if blocked:
            return blocked

        try:
            content = response.json()
        except ValueError:
            content = response.text

        return {
            "success": response.status_code < 400,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content": content,
            "url": str(response.url),
        }

    except httpx.TimeoutException:
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": f"Request timed out after {timeout} seconds",
            "url": url,
        }
    except httpx.HTTPError as e:
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": f"Request error: {e!s}",
            "url": url,
        }
