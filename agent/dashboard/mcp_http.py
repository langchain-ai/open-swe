"""DNS-pinned HTTPS transport for user-configured cloud MCP endpoints."""

import asyncio
import ipaddress
import json
import socket
from typing import Any

import httpx


class MCPConnectionError(Exception):
    """A safe error that route wrappers can return without upstream details."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate_url(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value)
        if (
            url.scheme != "https"
            or not url.host
            or url.userinfo
            or url.fragment
            or url.query
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError
        return url
    except TypeError, ValueError, httpx.InvalidURL:
        raise MCPConnectionError(
            400, "Use an HTTPS URL without credentials, query or fragment"
        ) from None


async def resolve_url(value: str) -> tuple[httpx.URL, str]:
    url = validate_url(value)
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            url.host, url.port or 443, type=socket.SOCK_STREAM
        )
        if not addresses:
            raise ValueError
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if isinstance(ip, ipaddress.IPv6Address):
                if ip.ipv4_mapped:
                    ip = ip.ipv4_mapped
                elif ip.sixtofour or ip.teredo or ip in ipaddress.ip_network("64:ff9b::/96"):
                    raise ValueError
            if not ip.is_global or ip.is_multicast or ip.is_unspecified:
                raise ValueError
        return url, str(addresses[0][4][0])
    except OSError, ValueError:
        raise MCPConnectionError(400, "Endpoint must resolve only to public addresses") from None


class PinnedTransport(httpx.AsyncBaseTransport):
    """Validate every request and connect to its resolved IP with original TLS SNI."""

    def __init__(self, endpoint: str | None = None) -> None:
        self.endpoint = str(validate_url(endpoint)) if endpoint else None
        self.transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.endpoint is not None and str(request.url) != self.endpoint:
            raise MCPConnectionError(400, "MCP endpoint changes require reconnecting")
        url, address = await resolve_url(str(request.url))
        headers = request.headers.copy()
        headers["Host"] = url.netloc.decode("ascii")
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=address),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        try:
            response = await self.transport.handle_async_request(pinned)
        except httpx.HTTPError:
            raise MCPConnectionError(502, "MCP endpoint request failed") from None
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise MCPConnectionError(
                502, "Endpoint redirects are not supported; use its canonical URL"
            )
        return response

    async def aclose(self) -> None:
        await self.transport.aclose()


def safe_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
    *,
    endpoint: str | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=PinnedTransport(endpoint),
        trust_env=False,
        follow_redirects=False,
        headers=headers,
        auth=auth,
        timeout=timeout or httpx.Timeout(30, read=300),
    )


async def request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        async with asyncio.timeout(30), safe_client(timeout=httpx.Timeout(20)) as client:
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
