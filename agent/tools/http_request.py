import contextlib
import ipaddress
import socket
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

_MAX_REDIRECTS = 5

# Serialises pinned DNS resolution. _pinned_dns_resolution swaps the global
# socket.getaddrinfo, so concurrent pin scopes inside the same process must not
# overlap. The lock is held only for the duration of a single HTTP request.
_DNS_PIN_LOCK = threading.Lock()


def _is_url_safe(url: str) -> tuple[bool, str, list[str]]:
    """Check if a URL is safe to request (not targeting private/internal networks).

    Returns ``(is_safe, reason, validated_ips)``. ``validated_ips`` is the list of
    resolved IP addresses that passed the SSRF policy; empty when ``is_safe`` is
    False. The list is what callers must pin DNS resolution to during the actual
    request — re-resolving on connect lets a short-TTL attacker DNS-rebind to a
    private address after this check passes.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False, f"Unsupported URL scheme: {parsed.scheme or '<missing>'}", []

        hostname = parsed.hostname
        if not hostname:
            return False, "Could not parse hostname from URL", []

        try:
            addr_infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False, f"Could not resolve hostname: {hostname}", []

        validated_ips: list[str] = []
        for addr_info in addr_infos:
            ip_str = addr_info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, f"URL resolves to blocked address: {ip_str}", []

            if ip_str not in validated_ips:
                validated_ips.append(ip_str)

        if not validated_ips:
            return False, f"No usable IP addresses for hostname: {hostname}", []

        return True, "", validated_ips
    except Exception as e:  # noqa: BLE001
        return False, f"URL validation error: {e}", []


def _build_pinned_addr_info(ip_str: str, port: int) -> tuple:
    """Build a single ``socket.getaddrinfo``-shaped tuple for a pre-validated IP."""
    ip = ipaddress.ip_address(ip_str)
    if ip.version == 4:
        return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip_str, port))
    return (
        socket.AF_INET6,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        "",
        (ip_str, port, 0, 0),
    )


@contextlib.contextmanager
def _pinned_dns_resolution(target_hostname: str, pinned_ips: list[str]) -> Iterator[None]:
    """Force ``socket.getaddrinfo(target_hostname, ...)`` to return ``pinned_ips``.

    Defeats DNS-rebinding bypass of the SSRF check (issue #1186). Without pinning,
    ``_is_url_safe`` resolves the hostname for validation, then ``requests.request``
    resolves it *again* when opening the TCP connection. An attacker controlling a
    short-TTL DNS record can answer the first query with a public IP and the second
    with an internal IP. With pinning, the second lookup returns the IPs the
    validator already approved.

    Lookups for hostnames *other* than ``target_hostname`` fall through to the
    system resolver, so unrelated DNS work in the same call stack is unaffected.
    The module-level lock serialises overlapping pin scopes inside the process.
    """
    target = target_hostname.lower()

    addr_infos_cache: dict[int, list[tuple]] = {}

    def _build_for_port(port: int) -> list[tuple]:
        cached = addr_infos_cache.get(port)
        if cached is not None:
            return cached
        infos: list[tuple] = []
        for ip_str in pinned_ips:
            try:
                infos.append(_build_pinned_addr_info(ip_str, port))
            except ValueError:
                continue
        addr_infos_cache[port] = infos
        return infos

    with _DNS_PIN_LOCK:
        original = socket.getaddrinfo

        def _pinned(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
            if host and str(host).lower() == target:
                infos = _build_for_port(int(port) if port else 0)
                if infos:
                    return infos
            return original(host, port, *args, **kwargs)

        socket.getaddrinfo = _pinned  # type: ignore[assignment]
        try:
            yield
        finally:
            socket.getaddrinfo = original  # type: ignore[assignment]


def _blocked_response(url: str, reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "status_code": 0,
        "headers": {},
        "content": f"Request blocked: {reason}",
        "url": url,
    }


def _request_with_safe_redirects(
    method: str,
    url: str,
    *,
    timeout: int,
    **kwargs: Any,
) -> tuple[requests.Response | None, dict[str, Any] | None]:
    """Issue a request while validating every redirect target before following it.

    Each hop validates the URL via ``_is_url_safe`` and then issues the actual
    network request inside ``_pinned_dns_resolution`` so the connect step uses
    the IPs the validator just approved (not a re-resolved value).
    """
    current_method = method.upper()
    current_url = url
    request_kwargs = dict(kwargs)

    for redirect_count in range(_MAX_REDIRECTS + 1):
        is_safe, reason, validated_ips = _is_url_safe(current_url)
        if not is_safe:
            return None, _blocked_response(current_url, reason)

        parsed = urlparse(current_url)
        hostname = parsed.hostname or ""

        with _pinned_dns_resolution(hostname, validated_ips):
            response = requests.request(
                current_method,
                current_url,
                timeout=timeout,
                allow_redirects=False,
                **request_kwargs,
            )

        if not response.is_redirect and not response.is_permanent_redirect:
            return response, None

        location = response.headers.get("Location")
        if not location:
            return response, None

        if redirect_count == _MAX_REDIRECTS:
            return None, _blocked_response(current_url, "Too many redirects")

        current_url = urljoin(str(response.url), location)

        if response.status_code == requests.codes.see_other or (
            response.status_code in {requests.codes.moved, requests.codes.found}
            and current_method not in {"GET", "HEAD"}
        ):
            current_method = "GET"
            request_kwargs.pop("data", None)
            request_kwargs.pop("json", None)

    return None, _blocked_response(current_url, "Too many redirects")


def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: str | dict | None = None,
    params: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Make HTTP requests to APIs and web services.

    Args:
        url: Target URL
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        headers: HTTP headers to include
        data: Request body data (string or dict)
        params: URL query parameters
        timeout: Request timeout in seconds

    Returns:
        Dictionary with response data including status, headers, and content
    """
    try:
        kwargs: dict[str, Any] = {}

        if headers:
            kwargs["headers"] = headers
        if params:
            kwargs["params"] = params
        if data:
            if isinstance(data, dict):
                kwargs["json"] = data
            else:
                kwargs["data"] = data

        response, blocked = _request_with_safe_redirects(
            method,
            url,
            timeout=timeout,
            **kwargs,
        )
        if blocked:
            return blocked

        try:
            content = response.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            content = response.text

        return {
            "success": response.status_code < 400,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content": content,
            "url": response.url,
        }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": f"Request timed out after {timeout} seconds",
            "url": url,
        }
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": f"Request error: {e!s}",
            "url": url,
        }
