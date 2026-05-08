import contextlib
import ipaddress
import socket
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

_MAX_REDIRECTS = 5

_real_getaddrinfo = socket.getaddrinfo
_pin_state = threading.local()
_patch_lock = threading.Lock()
_patch_installed = False


def _get_pin_stack() -> list[dict[str, list]]:
    stack = getattr(_pin_state, "stack", None)
    if stack is None:
        stack = []
        _pin_state.stack = stack
    return stack


def _pinned_getaddrinfo(host, port, *args, **kwargs):
    stack = _get_pin_stack()
    if stack:
        pins = stack[-1]
        if host in pins:
            results = []
            for family, socktype, proto, canonname, sockaddr in pins[host]:
                if port is None:
                    new_sockaddr = sockaddr
                elif family == socket.AF_INET:
                    new_sockaddr = (sockaddr[0], int(port))
                elif family == socket.AF_INET6:
                    rest = sockaddr[2:] if len(sockaddr) >= 4 else (0, 0)
                    new_sockaddr = (sockaddr[0], int(port), *rest)
                else:
                    new_sockaddr = sockaddr
                results.append((family, socktype, proto, canonname, new_sockaddr))
            return results
    return _real_getaddrinfo(host, port, *args, **kwargs)


def _ensure_dns_patch_installed() -> None:
    global _patch_installed
    if _patch_installed:
        return
    with _patch_lock:
        if _patch_installed:
            return
        socket.getaddrinfo = _pinned_getaddrinfo
        _patch_installed = True


@contextlib.contextmanager
def _pin_dns(hostname: str, addr_infos: list) -> Iterator[None]:
    """Force socket.getaddrinfo(hostname, ...) to return the pre-validated addresses.

    Other hostnames pass through to the real resolver. Scope is per-thread, so
    concurrent requests on other threads are unaffected.
    """
    _ensure_dns_patch_installed()
    stack = _get_pin_stack()
    pins: dict[str, list] = dict(stack[-1]) if stack else {}
    pins[hostname] = addr_infos
    stack.append(pins)
    try:
        yield
    finally:
        stack.pop()


def _resolve_and_validate(url: str) -> tuple[bool, str, str | None, list | None]:
    """Resolve a URL's hostname and check every address is safe to contact.

    Returns (is_safe, reason, hostname, addr_infos). When safe, the caller must
    use _pin_dns(hostname, addr_infos) so the subsequent connection cannot pick
    up a different (e.g. DNS-rebound) address.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False, f"Unsupported URL scheme: {parsed.scheme or '<missing>'}", None, None

        hostname = parsed.hostname
        if not hostname:
            return False, "Could not parse hostname from URL", None, None

        try:
            addr_infos = _real_getaddrinfo(hostname, None)
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

            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, f"URL resolves to blocked address: {ip_str}", hostname, None

        return True, "", hostname, addr_infos
    except Exception as e:  # noqa: BLE001
        return False, f"URL validation error: {e}", None, None


def _is_url_safe(url: str) -> tuple[bool, str]:
    """Check if a URL is safe to request (not targeting private/internal networks)."""
    is_safe, reason, _, _ = _resolve_and_validate(url)
    return is_safe, reason


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

    The hostname is resolved once per hop and every subsequent DNS lookup for
    that hostname (inside requests/urllib3) is pinned to the validated
    addresses. This closes the DNS-rebinding race where a controlled resolver
    returns a public IP at validation time and a private IP at connect time.
    """
    current_method = method.upper()
    current_url = url
    request_kwargs = dict(kwargs)

    for redirect_count in range(_MAX_REDIRECTS + 1):
        is_safe, reason, hostname, addr_infos = _resolve_and_validate(current_url)
        if not is_safe or hostname is None or addr_infos is None:
            return None, _blocked_response(current_url, reason)

        with _pin_dns(hostname, addr_infos):
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

    Do not use this tool for GitHub API calls. Use `GH_TOKEN=dummy gh` in the
    sandbox so GitHub authentication is handled by the sandbox proxy.

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
