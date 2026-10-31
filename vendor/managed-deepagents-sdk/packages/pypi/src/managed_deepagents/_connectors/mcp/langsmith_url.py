"""Detect LangSmith Managed Tools MCP URLs without trusting path alone."""

from __future__ import annotations

import re
from urllib.parse import ParseResult, urlparse

_LANGSMITH_MCP_PATH = re.compile(
    r"^/v1/managed-tools/(?:servers|kits|gateways)/[A-Za-z0-9][A-Za-z0-9_-]{0,127}/mcp/?$"
)
_DEFAULT_PORT = {"http": 80, "https": 443}


def _origin(parsed: ParseResult) -> str | None:
    if parsed.scheme not in _DEFAULT_PORT or parsed.hostname is None:
        return None
    port = parsed.port
    if port is None or port == _DEFAULT_PORT[parsed.scheme]:
        return f"{parsed.scheme}://{parsed.hostname}"
    return f"{parsed.scheme}://{parsed.hostname}:{port}"


def _http_url(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url.strip())
    origin = _origin(parsed)
    if origin is None:
        return None
    if parsed.username or parsed.password or parsed.fragment:
        return None
    return origin, parsed.path


def is_langsmith_managed_tools_path(url: object) -> bool:
    """True when ``url`` is an LMT MCP path, regardless of host."""
    if not isinstance(url, str):
        return False
    parsed = _http_url(url)
    return parsed is not None and bool(_LANGSMITH_MCP_PATH.fullmatch(parsed[1]))


def is_langsmith_managed_tools_url(url: object, origin: str) -> bool:
    """True when ``url`` is an LMT MCP path on ``origin`` (no secrets for other hosts)."""
    if not isinstance(url, str):
        return False
    parsed = _http_url(url)
    if parsed is None or not _LANGSMITH_MCP_PATH.fullmatch(parsed[1]):
        return False
    expected = _origin(urlparse(origin.strip()))
    return expected is not None and parsed[0] == expected
