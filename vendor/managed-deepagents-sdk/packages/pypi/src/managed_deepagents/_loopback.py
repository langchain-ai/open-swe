"""Resolve the local Agent Server URL for trusted loopback clients."""

from __future__ import annotations

import ipaddress
import os
import urllib.parse


def resolve_loopback_api_url(*, explicit: str | None = None) -> str:
    """Return the Agent Server base URL for in-process/loopback SDK clients.

    Prefers ``explicit``, then ``MDA_CHANNEL_API_URL`` / ``LANGGRAPH_API_URL``,
    else ``http://127.0.0.1:{PORT|8000}{MOUNT_PREFIX}``. ``LANGSMITH_ENDPOINT`` is deliberately
    not consulted: it is the tracing/API endpoint (LangSmith SaaS by default),
    not this server's origin.
    """
    from_env = (
        explicit or os.environ.get("MDA_CHANNEL_API_URL") or os.environ.get("LANGGRAPH_API_URL")
    )
    if from_env and from_env.strip():
        return from_env.strip().rstrip("/")
    port = os.environ.get("PORT") or "8000"
    prefix = os.environ.get("MOUNT_PREFIX", "").rstrip("/")
    return f"http://127.0.0.1:{port}{prefix if prefix.startswith('/') else ''}"


def resolve_loopback_api_key(*, explicit: str | None = None) -> str:
    """API key for trusted loopback LangGraph SDK clients.

    Prefers ``explicit``, then ``LANGSMITH_API_KEY``, else ``"local"``
    (in-process / unit-test clients that do not authenticate against Host).
    """
    key = (explicit if explicit is not None else os.environ.get("LANGSMITH_API_KEY") or "").strip()
    return key or "local"


def is_loopback_url(value: str) -> bool:
    """Whether ``value`` addresses this host.

    Server-local trust (the ingress secret, which lets its holder assert any
    actor id) must never travel to an off-host origin, so callers gate it on
    this predicate. An unparseable value (``http://[::1``) is not this host.
    """
    try:
        parsed = urllib.parse.urlparse(value if "://" in value else f"http://{value}")
    except ValueError:
        return False
    return is_loopback_host(parsed.hostname or "")


def sends_key_securely(url: str) -> bool:
    """Whether a credential sent to ``url`` would travel encrypted.

    That is https, or plain http only to this machine.
    """
    scheme = urllib.parse.urlsplit(url).scheme
    return scheme == "https" or (scheme == "http" and is_loopback_url(url))


def is_loopback_host(host: str) -> bool:
    """Whether a ``Host``-style value (optional port, optional brackets) is loopback."""
    hostname = host.strip().lower()
    if hostname.startswith("[") and "]" in hostname:
        hostname = hostname[1 : hostname.index("]")]
    elif hostname.count(":") == 1:
        hostname = hostname.split(":", 1)[0]
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return hostname == "localhost"


__all__ = [
    "is_loopback_host",
    "is_loopback_url",
    "resolve_loopback_api_key",
    "resolve_loopback_api_url",
    "sends_key_securely",
]
