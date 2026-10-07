"""Public HTTPS transport for admin-configured remote MCP connections."""

import asyncio
from urllib.parse import urlsplit

import httpx

from openswe.utils.url_safety import UnsafeUrlError, pinned_url, resolve_and_validate


class MCPDiscoveryError(ValueError):
    """A failure whose message we wrote, so it is safe to show the person configuring the MCP."""


class MCPTransport(httpx.AsyncBaseTransport):
    def __init__(self, url: str) -> None:
        parsed = urlsplit(url)
        self._origin = (parsed.scheme, parsed.hostname, parsed.port or 443)
        self._transport = httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        parsed = urlsplit(url)
        if (
            parsed.scheme,
            parsed.hostname,
            parsed.port or 443,
        ) != self._origin or parsed.scheme != "https":
            raise MCPDiscoveryError("MCP requests must stay on the configured HTTPS origin")
        try:
            hostname, public_ips = await asyncio.to_thread(resolve_and_validate, url)
        except UnsafeUrlError as exc:
            raise MCPDiscoveryError("MCP server must resolve only to public addresses") from exc
        # Pin the checked address so a second DNS lookup cannot reach a private host.
        headers = request.headers.copy()
        headers["Host"] = parsed.netloc
        for index, address in enumerate(public_ips):
            pinned = httpx.Request(
                request.method,
                pinned_url(url, address),
                headers=headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": hostname},
            )
            try:
                return await self._transport.handle_async_request(pinned)
            except httpx.ConnectError, httpx.ConnectTimeout:
                if index == len(public_ips) - 1:
                    raise
        raise httpx.ConnectError("MCP server has no public addresses")

    async def aclose(self) -> None:
        await self._transport.aclose()


def mcp_http_client(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Synchronous factory required by the MCP adapter; all network I/O is async."""
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(30),
        auth=auth,
        transport=MCPTransport(url),
        follow_redirects=False,
        trust_env=False,
    )
