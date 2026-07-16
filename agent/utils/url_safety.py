"""Shared SSRF guard: resolve a URL's host and confirm it is publicly routable.

Used by the ``http_request`` tool (which additionally pins the connection and
re-validates every redirect hop) and by server-side image fetching, so an
untrusted URL can't reach internal services or the cloud metadata endpoint.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def resolve_and_validate(url: str) -> tuple[bool, str, str | None, list | None]:
    """Resolve a URL's hostname and check every address is safe to contact.

    Returns (is_safe, reason, hostname, addr_infos). When safe, the caller pins
    the connection to one of ``addr_infos`` so the request cannot pick up a
    different (e.g. DNS-rebound) address after validation.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False, f"Unsupported URL scheme: {parsed.scheme or '<missing>'}", None, None

        hostname = parsed.hostname
        if not hostname:
            return False, "Could not parse hostname from URL", None, None

        try:
            addr_infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False, f"Could not resolve hostname: {hostname}", hostname, None

        if not addr_infos:
            return False, f"Could not resolve hostname: {hostname}", hostname, None

        for addr_info in addr_infos:
            ip_str = addr_info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return False, f"Could not parse resolved address: {ip_str}", hostname, None

            # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) so a mapped private
            # address can't slip past the check, then block anything that isn't
            # publicly routable (covers private/loopback/link-local/reserved/
            # unspecified/multicast and the cloud metadata 169.254.0.0/16 range).
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
            if not ip.is_global:
                return False, f"URL resolves to blocked address: {ip_str}", hostname, None

        return True, "", hostname, addr_infos
    except Exception as e:  # noqa: BLE001
        return False, f"URL validation error: {e}", None, None


def is_url_safe(url: str) -> tuple[bool, str]:
    """Check if a URL is safe to request (not targeting private/internal networks)."""
    is_safe, reason, _, _ = resolve_and_validate(url)
    return is_safe, reason
