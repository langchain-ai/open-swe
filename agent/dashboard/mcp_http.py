"""URL validation and safe JSON requests for user-configured cloud MCP endpoints."""

import asyncio
import json
from typing import Any

import httpx

from agent.tool_loaders.mcp_transport import mcp_http_client
from agent.utils.url_safety import resolve_and_validate


class MCPConnectionError(Exception):
    """A safe error that route wrappers can return without upstream details."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate_url(value: str, *, allow_query: bool = False) -> httpx.URL:
    try:
        url = httpx.URL(value)
        if (
            url.scheme != "https"
            or not url.host
            or url.userinfo
            or url.fragment
            or (url.query and not allow_query)
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError
        return url
    except TypeError, ValueError, httpx.InvalidURL:
        raise MCPConnectionError(
            400, "Use an HTTPS URL without credentials, query or fragment"
        ) from None


async def resolve_url(value: str, *, allow_query: bool = False) -> httpx.URL:
    url = validate_url(value, allow_query=allow_query)
    safe, _, _, _ = await asyncio.to_thread(resolve_and_validate, str(url))
    if not safe:
        raise MCPConnectionError(400, "Endpoint must resolve only to public addresses")
    return url


async def request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        async with asyncio.timeout(30), mcp_http_client(timeout=httpx.Timeout(20)) as client:
            async with client.stream(method, url, **kwargs) as response:
                if not response.is_success:
                    raise MCPConnectionError(502, "OAuth endpoint rejected the request")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_048_576:
                        raise MCPConnectionError(502, "OAuth response exceeds the size limit")
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError
                return data
    except httpx.HTTPError, ValueError, TimeoutError:
        raise MCPConnectionError(502, "Invalid OAuth endpoint response") from None
