from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

_MAX_REDIRECTS = 5
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_ENTITY_HEADERS = {"content-encoding", "content-language", "content-length", "content-type"}
_SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization"}


def resolve_and_validate(url: str) -> tuple[bool, str, str | None, list | None]:
    """Resolve a URL host and require every resolved address to be public."""
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

            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
            if not ip.is_global:
                return False, f"URL resolves to blocked address: {ip_str}", hostname, None

        return True, "", hostname, addr_infos
    except Exception as e:
        return False, f"URL validation error: {e}", None, None


def is_url_safe(url: str) -> tuple[bool, str]:
    """Check if a URL is safe to request."""
    is_safe, reason, _, _ = resolve_and_validate(url)
    return is_safe, reason


def _blocked_response(url: str, reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "status_code": 0,
        "headers": {},
        "content": f"Request blocked: {reason}",
        "url": url,
    }


def pinned_url(url: str, ip: str) -> str:
    parsed = urlparse(url)
    host_literal = f"[{ip}]" if ":" in ip else ip
    netloc = f"{host_literal}:{parsed.port}" if parsed.port else host_literal
    return urlunparse(parsed._replace(netloc=netloc))


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        port = -1
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _is_sensitive_header(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in _SENSITIVE_HEADERS or "api-key" in normalized or normalized.endswith("-token")
    )


def _redirect_method(method: str, status_code: int) -> str:
    if status_code == 303 and method != "HEAD":
        return "GET"
    if status_code == 302 and method != "HEAD":
        return "GET"
    if status_code == 301 and method == "POST":
        return "GET"
    return method


async def request_with_safe_redirects(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers_for_url: Callable[[str, str], Mapping[str, str] | None] | None = None,
    **kwargs: Any,
) -> tuple[httpx.Response | None, dict[str, Any] | None]:
    """Issue a request with DNS pinning and per-hop redirect validation."""
    current_method = method.upper()
    current_url = url
    request_kwargs = dict(kwargs)
    caller_headers = dict(request_kwargs.pop("headers", None) or {})
    caller_extensions = dict(request_kwargs.pop("extensions", None) or {})
    response: httpx.Response | None = None

    for redirect_count in range(_MAX_REDIRECTS + 1):
        is_safe, reason, hostname, addr_infos = resolve_and_validate(current_url)
        if not is_safe or hostname is None or addr_infos is None:
            return None, _blocked_response(current_url, reason)

        parsed = urlparse(current_url)
        per_hop_headers = dict(headers_for_url(url, current_url) or {}) if headers_for_url else {}
        headers = {**caller_headers, **per_hop_headers, "Host": parsed.netloc}
        extensions = {**caller_extensions, "sni_hostname": hostname}

        pinned_ips = list(dict.fromkeys(addr_info[4][0] for addr_info in addr_infos))
        for address_index, pinned_ip in enumerate(pinned_ips):
            try:
                response = await client.request(
                    current_method,
                    pinned_url(current_url, pinned_ip),
                    follow_redirects=False,
                    headers=headers,
                    extensions=extensions,
                    **request_kwargs,
                )
                break
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if address_index == len(pinned_ips) - 1:
                    raise

        if response is None:
            raise httpx.ConnectError("No response received from pinned address")
        if response.status_code not in _REDIRECT_CODES:
            return response, None

        location = response.headers.get("Location")
        if not location:
            return response, None

        if redirect_count == _MAX_REDIRECTS:
            return None, _blocked_response(current_url, "Too many redirects")

        next_url = urljoin(current_url, location)
        request_kwargs.pop("params", None)
        if _origin(current_url) != _origin(next_url):
            caller_headers = {
                name: value
                for name, value in caller_headers.items()
                if not _is_sensitive_header(name)
            }
            request_kwargs.pop("auth", None)
            request_kwargs.pop("cookies", None)

        next_method = _redirect_method(current_method, response.status_code)
        if next_method != current_method:
            caller_headers = {
                name: value
                for name, value in caller_headers.items()
                if name.lower() not in _ENTITY_HEADERS
            }
            request_kwargs.pop("data", None)
            request_kwargs.pop("content", None)
            request_kwargs.pop("json", None)

        current_method = next_method
        current_url = next_url

    return None, _blocked_response(current_url, "Too many redirects")
