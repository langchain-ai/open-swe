"""One connection pool per event loop, shared by the HTTP clients made per call.

A fresh ``httpx2.AsyncClient`` or ``aiohttp.ClientSession`` opens its own TCP and
TLS connection, so code that built one per request (or per ``async with`` block)
paid a full handshake every time. A ``PooledAsyncClient``, or a session built with
``connector=pooled_connector(), connector_owner=False``, keeps its own headers,
auth and timeouts but draws connections from the running loop's pool, which
keeps them warm across calls. Closing such a client leaves the pool open.
"""

import asyncio
import ssl
import weakref

import aiohttp
import httpx2
from httpx2._types import CertTypes

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
_POOLS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx2.AsyncHTTPTransport] = (
    weakref.WeakKeyDictionary()
)
# The Slack SDK only speaks aiohttp, whose sessions draw from a connector instead.
_CONNECTORS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aiohttp.TCPConnector] = (
    weakref.WeakKeyDictionary()
)


def _pool() -> httpx2.AsyncHTTPTransport:
    loop = asyncio.get_running_loop()
    pool = _POOLS.get(loop)
    if pool is None:
        pool = _POOLS[loop] = httpx2.AsyncHTTPTransport(limits=_LIMITS)
    return pool


class _PooledTransport(httpx2.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return await _pool().handle_async_request(request)

    async def aclose(self) -> None:
        # The pool outlives any one client; ``close_pool`` closes it.
        return None


_POOLED_TRANSPORT = _PooledTransport()


class PooledAsyncClient(httpx2.AsyncClient):
    """An HTTP client with isolated request settings and shared warm connections."""

    def _init_transport(
        self,
        verify: ssl.SSLContext | str | bool = True,
        cert: CertTypes | None = None,
        trust_env: bool = True,
        http1: bool = True,
        http2: bool = False,
        limits: httpx2.Limits = _LIMITS,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> httpx2.AsyncBaseTransport:
        return transport if transport is not None else _POOLED_TRANSPORT


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
    if (pool := _POOLS.pop(loop, None)) is not None:
        await pool.aclose()
    if (connector := _CONNECTORS.pop(loop, None)) is not None:
        await connector.close()
