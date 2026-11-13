"""One connection pool per event loop, shared by the HTTP clients made per call.

A fresh ``httpx2.AsyncClient`` or ``aiohttp.ClientSession`` opens its own TCP and
TLS connection, so code that built one per request (or per ``async with`` block)
paid a full handshake every time. A client built with
``transport=pooled_transport()``, or a session built with
``connector=pooled_connector(), connector_owner=False``, keeps its own headers,
auth and timeouts but draws connections from the running loop's pool, which
keeps them warm across calls. Closing such a client leaves the pool open.
"""

import asyncio
import ipaddress
import weakref
from urllib.request import getproxies, proxy_bypass

import aiohttp
import httpx2

# httpx2 drops idle connections after 5 s; an agent's calls to the same host are
# often further apart than that.
_KEEPALIVE_SECONDS = 30.0
_MAX_CONNECTIONS = 200
_LIMITS = httpx2.Limits(
    max_connections=_MAX_CONNECTIONS,
    max_keepalive_connections=50,
    keepalive_expiry=_KEEPALIVE_SECONDS,
)
# Each isolated-loop worker thread runs its own loop, and a connection belongs to
# the loop that opened it.
_POOLS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, str | None], httpx2.AsyncHTTPTransport]
] = weakref.WeakKeyDictionary()
# The Slack SDK only speaks aiohttp, whose sessions draw from a connector instead.
_CONNECTORS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aiohttp.TCPConnector] = (
    weakref.WeakKeyDictionary()
)


class _PooledTransport(httpx2.AsyncBaseTransport):
    def __init__(self, name: str, limits: httpx2.Limits, proxy: str | None) -> None:
        self.name = name
        self.limits = limits
        self.proxy = proxy

    @staticmethod
    def environment_proxy(url: httpx2.URL) -> str | None:
        proxies = getproxies()
        if proxy_bypass(url.netloc.decode()):
            return None
        try:
            address = ipaddress.ip_address(url.host)
        except ValueError:
            address = None
        if address is not None:
            for host in proxies.get("no", "").split(","):
                if "/" not in host:
                    continue
                try:
                    network = ipaddress.ip_network(host.strip(), strict=False)
                except ValueError:
                    continue
                if address in network:
                    return None
        proxy = proxies.get(url.scheme) or proxies.get("all")
        return f"http://{proxy}" if proxy and "://" not in proxy else proxy

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        pools = _POOLS.setdefault(asyncio.get_running_loop(), {})
        key = (self.name, self.proxy)
        pool = pools.get(key)
        if pool is None:
            pool = pools[key] = httpx2.AsyncHTTPTransport(limits=self.limits, proxy=self.proxy)
        return await pool.handle_async_request(request)

    async def aclose(self) -> None:
        return None


def pooled_transport(
    name: str = "default",
    *,
    limits: httpx2.Limits = _LIMITS,
    proxy: str | None = None,
    url: httpx2.URL | None = None,
) -> httpx2.AsyncBaseTransport:
    """Borrow an event-loop pool, optionally isolated by traffic class and proxy."""
    if url is not None:
        proxy = _PooledTransport.environment_proxy(url)
    return _PooledTransport(name, limits, proxy)


def pooled_connector() -> aiohttp.TCPConnector:
    """The connector for a per-call aiohttp session; pass ``connector_owner=False`` with it."""
    loop = asyncio.get_running_loop()
    connector = _CONNECTORS.get(loop)
    if connector is None or connector.closed:
        connector = _CONNECTORS[loop] = aiohttp.TCPConnector(
            limit=_MAX_CONNECTIONS, keepalive_timeout=_KEEPALIVE_SECONDS
        )
    return connector


async def close_pool() -> None:
    """Close this event loop's pools; the next request opens new ones."""
    loop = asyncio.get_running_loop()
    for pool in _POOLS.pop(loop, {}).values():
        await pool.aclose()
    if (connector := _CONNECTORS.pop(loop, None)) is not None:
        await connector.close()
