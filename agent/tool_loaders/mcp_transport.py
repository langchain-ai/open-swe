"""Public HTTPS transport shared by workspace, per-user and OAuth MCP requests."""

import asyncio
from urllib.parse import urlsplit

import httpx

from agent.utils.url_safety import pinned_url, resolve_and_validate


def _origin(url: str) -> tuple[str, str | None, int]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or 443


class MCPTransport(httpx.AsyncBaseTransport):
    """Pin every request to a public, freshly validated address.

    With ``url`` the transport additionally refuses to leave that origin, which
    keeps a server from redirecting saved credentials elsewhere. Without it
    (OAuth metadata discovery spans several hosts) only the HTTPS and
    public-address rules apply.
    """

    def __init__(self, url: str | None = None) -> None:
        self._origin = _origin(url) if url else None
        self._transport = httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or (self._origin is not None and _origin(url) != self._origin):
            raise ValueError("MCP requests must stay on the configured HTTPS origin")
        safe, _, hostname, addresses = await asyncio.to_thread(resolve_and_validate, url)
        if not safe or not hostname or not addresses:
            raise ValueError("MCP server must resolve only to public addresses")
        # Pin the checked address so a second DNS lookup cannot reach a private host.
        headers = request.headers.copy()
        headers["Host"] = parsed.netloc
        public_ips = list(dict.fromkeys(address[4][0] for address in addresses))
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
    url: str | None = None,
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
