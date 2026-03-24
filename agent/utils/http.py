"""Shared httpx client cache for connection pooling."""

from __future__ import annotations

import httpx

_CLIENT_CACHE_MAX_SIZE = 10
_CLIENT_CACHE: dict[str, httpx.AsyncClient] = {}


def _create_http_client(base_url: str, timeout: float = 10.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(
            retries=3,
            limits=httpx.Limits(max_keepalive_connections=40, keepalive_expiry=240.0),
        ),
        timeout=httpx.Timeout(timeout),
        base_url=base_url,
    )


def get_http_client(base_url: str = "", timeout: float = 10.0) -> httpx.AsyncClient:
    """Get or create a cached HTTP client for the given base URL.

    Returns a long-lived AsyncClient that reuses TCP connections.
    Do NOT use this with ``async with`` — the client is shared and must not be closed.
    """
    if base_url not in _CLIENT_CACHE:
        if len(_CLIENT_CACHE) >= _CLIENT_CACHE_MAX_SIZE:
            _CLIENT_CACHE.popitem()
        _CLIENT_CACHE[base_url] = _create_http_client(base_url, timeout)

    cached_client = _CLIENT_CACHE[base_url]
    if cached_client.is_closed:
        _CLIENT_CACHE[base_url] = _create_http_client(base_url, timeout)

    return _CLIENT_CACHE[base_url]
