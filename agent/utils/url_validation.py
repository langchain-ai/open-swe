"""URL validation utilities for SSRF prevention."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def validate_url(
    url: str,
    *,
    allowed_schemes: frozenset[str] = frozenset({"https"}),
) -> tuple[str, int, str] | None:
    """Resolve a URL and validate it is safe to fetch (no SSRF).

    Resolves DNS once and returns the first globally-routable IP so the
    caller can connect directly to that address, avoiding DNS-rebinding
    (TOCTOU) attacks.

    Args:
        url: The URL to validate.
        allowed_schemes: Permitted URL schemes (default: HTTPS only).

    Returns:
        ``(ip, port, hostname)`` on success, or ``None`` if the URL is
        unsafe or cannot be resolved.
    """
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return None

    if parsed.scheme not in allowed_schemes:
        logger.warning("Blocked URL with disallowed scheme %r: %s", parsed.scheme, url)
        return None

    hostname = parsed.hostname
    if not hostname:
        return None

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    # Resolve hostname to IP addresses
    try:
        addrinfos = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        logger.warning("DNS resolution failed for URL host: %s", hostname)
        return None

    if not addrinfos:
        return None

    # Validate ALL resolved addresses are globally routable, then pick the
    # first one.  We check all of them to prevent an attacker from mixing
    # public and private records.
    first_ip: str | None = None
    for _family, _, _, _, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return None

        if not addr.is_global:
            logger.warning(
                "Blocked URL %s: resolved to non-global IP %s", url, ip_str
            )
            return None

        if first_ip is None:
            first_ip = ip_str

    if first_ip is None:
        return None

    return first_ip, port, hostname
