import contextlib
import ipaddress
import socket
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from urllib3.util import connection as urllib3_connection

_MAX_REDIRECTS = 5

_pin_state = threading.local()
_install_lock = threading.Lock()
_install_count = 0
_original_create_connection = None


def _get_pin_stack() -> list[dict[str, list]]:
    stack = getattr(_pin_state, "stack", None)
    if stack is None:
        stack = []
        _pin_state.stack = stack
    return stack


def _pinned_create_connection(
    address,
    timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address=None,
    socket_options=None,
):
    """Drop-in for urllib3.util.connection.create_connection that honors DNS pins.

    When the calling thread has an active _pin_dns context for this host, the
    connection uses the pre-validated addresses instead of calling
    socket.getaddrinfo again — closing the DNS-rebinding race.

    `timeout` and `socket_options` are accepted positionally because urllib3
    calls create_connection with timeout positional; reading them from kwargs
    only would silently drop the caller's connect timeout and TCP options.
    """
    host, port = address
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    stack = _get_pin_stack()
    pins = stack[-1] if stack else None
    pinned = pins.get(host) if pins else None

    if pinned is None:
        return _original_create_connection(
            address,
            timeout,
            source_address=source_address,
            socket_options=socket_options,
        )

    err = None
    for family, socktype, proto, _canonname, sockaddr in pinned:
        if family == socket.AF_INET:
            target = (sockaddr[0], port)
        elif family == socket.AF_INET6:
            rest = sockaddr[2:] if len(sockaddr) >= 4 else (0, 0)
            target = (sockaddr[0], port, *rest)
        else:
            continue

        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            for opt in socket_options or ():
                sock.setsockopt(*opt)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            return sock
        except OSError as e:
            err = e
            if sock is not None:
                sock.close()

    if err is not None:
        raise err
    raise OSError("DNS pin produced no usable addresses")


@contextlib.contextmanager
def _pin_dns(hostname: str, addr_infos: list) -> Iterator[None]:
    """Pin DNS resolution for `hostname` to `addr_infos` for the duration of the block.

    The patch is scoped to urllib3's connection helper (not socket-wide) and is
    installed on first entry / removed on last exit via reference counting, so
    no global mutation persists once no http_request calls are in flight.
    Other hostnames pass through to the original resolver. Per-thread scope
    (`threading.local`) keeps concurrent requests on other threads unaffected.
    """
    global _install_count, _original_create_connection

    with _install_lock:
        if _install_count == 0:
            _original_create_connection = urllib3_connection.create_connection
            urllib3_connection.create_connection = _pinned_create_connection
        _install_count += 1

    stack = _get_pin_stack()
    pins: dict[str, list] = dict(stack[-1]) if stack else {}
    pins[hostname] = addr_infos
    stack.append(pins)

    try:
        yield
    finally:
        stack.pop()
        with _install_lock:
            _install_count -= 1
            if _install_count == 0 and _original_create_connection is not None:
                urllib3_connection.create_connection = _original_create_connection
                _original_create_connection = None


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

    The hostname is resolved once per hop and the connection is forced to use
    the validated addresses, closing the DNS-rebinding race where a controlled
    resolver returns a public IP at validation time and a private IP at connect
    time.
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
