"""Public HTTPS transport for admin-configured remote MCP connections."""

import asyncio
from urllib.parse import urlsplit

import httpx2

from openswe.utils.url_safety import UnsafeUrlError, pinned_url, resolve_and_validate


class MCPDiscoveryError(ValueError):
    """A failure whose message we wrote, so it is safe to show the person configuring the MCP."""


class MCPTransport(httpx2.AsyncBaseTransport):
    def __init__(self, url: str) -> None:
        parsed = urlsplit(url)
        self._origin = (parsed.scheme, parsed.hostname, parsed.port or 443)
        self._transport = httpx2.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
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
            pinned = httpx2.Request(
                request.method,
                pinned_url(url, address),
                headers=headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": hostname},
            )
            try:
                return await self._transport.handle_async_request(pinned)
            except httpx2.ConnectError, httpx2.ConnectTimeout:
                if index == len(public_ips) - 1:
                    raise
        raise httpx2.ConnectError("MCP server has no public addresses")

    async def aclose(self) -> None:
        await self._transport.aclose()


def mcp_http_client(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    follow_redirects: bool = False,  # noqa: ARG001
) -> httpx2.AsyncClient:
    """Synchronous factory required by the MCP client; all network I/O is async.

    FastMCP asks for redirects, but they stay off so every request is origin-checked.
    """
    return httpx2.AsyncClient(
        headers=headers,
        timeout=timeout or httpx2.Timeout(30),
        auth=auth,
        transport=MCPTransport(url),
        follow_redirects=False,
        trust_env=False,
    )
