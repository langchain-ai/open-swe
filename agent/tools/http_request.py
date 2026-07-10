from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from ..utils.url_safety import resolve_and_validate as _resolve_and_validate

_MAX_REDIRECTS = 5

_REDIRECT_CODES = {301, 302, 303, 307, 308}

HTTP_REQUEST_MAX_CHARS = 100_000


def _truncate_content(content: Any) -> Any:
    """Cap string response bodies so oversized results don't flood model context."""
    if isinstance(content, str) and len(content) > HTTP_REQUEST_MAX_CHARS:
        return (
            content[:HTTP_REQUEST_MAX_CHARS] + "\n... [content truncated: "
            f"{HTTP_REQUEST_MAX_CHARS}/{len(content)} chars]\n"
        )
    return content


def _blocked_response(url: str, reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "status_code": 0,
        "headers": {},
        "content": f"Request blocked: {reason}",
        "url": url,
    }


def _pinned_url(url: str, ip: str) -> str:
    """Rewrite ``url`` so the connection targets ``ip`` while keeping the path/query.

    The original hostname is preserved separately for the ``Host`` header and TLS
    SNI/cert verification (via httpx's ``sni_hostname`` request extension).
    """
    parsed = urlparse(url)
    host_literal = f"[{ip}]" if ":" in ip else ip
    netloc = f"{host_literal}:{parsed.port}" if parsed.port else host_literal
    return urlunparse(parsed._replace(netloc=netloc))


async def _request_with_safe_redirects(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> tuple[httpx.Response | None, dict[str, Any] | None]:
    """Issue a request while validating every redirect target before following it.

    The hostname is resolved once per hop and the connection is pinned to the
    validated IP, closing the DNS-rebinding race where a controlled resolver
    returns a public IP at validation time and a private IP at connect time.
    """
    current_method = method.upper()
    current_url = url
    request_kwargs = dict(kwargs)
    # Pop caller headers/extensions ONCE so they're reused on every redirect hop
    # (the per-hop Host + SNI are layered on top each time). Popping inside the
    # loop dropped the caller's Authorization/Accept/etc. on the first redirect.
    caller_headers = dict(request_kwargs.pop("headers", None) or {})
    caller_extensions = dict(request_kwargs.pop("extensions", None) or {})

    for redirect_count in range(_MAX_REDIRECTS + 1):
        is_safe, reason, hostname, addr_infos = _resolve_and_validate(current_url)
        if not is_safe or hostname is None or addr_infos is None:
            return None, _blocked_response(current_url, reason)

        pinned_ip = addr_infos[0][4][0]
        parsed = urlparse(current_url)
        headers = {**caller_headers, "Host": parsed.netloc}
        extensions = {**caller_extensions, "sni_hostname": hostname}

        response = await client.request(
            current_method,
            _pinned_url(current_url, pinned_ip),
            follow_redirects=False,
            headers=headers,
            extensions=extensions,
            **request_kwargs,
        )

        if response.status_code not in _REDIRECT_CODES:
            return response, None

        location = response.headers.get("Location")
        if not location:
            return response, None

        if redirect_count == _MAX_REDIRECTS:
            return None, _blocked_response(current_url, "Too many redirects")

        current_url = urljoin(current_url, location)

        if response.status_code == 303 or (
            response.status_code in {301, 302} and current_method not in {"GET", "HEAD"}
        ):
            current_method = "GET"
            request_kwargs.pop("data", None)
            request_kwargs.pop("content", None)
            request_kwargs.pop("json", None)

    return None, _blocked_response(current_url, "Too many redirects")


async def http_request(
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
                kwargs["content"] = data

        async with httpx.AsyncClient(timeout=timeout) as client:
            response, blocked = await _request_with_safe_redirects(
                client,
                method,
                url,
                **kwargs,
            )
        if blocked:
            return blocked

        if len(response.text) > HTTP_REQUEST_MAX_CHARS:
            content: Any = _truncate_content(response.text)
        else:
            try:
                content = response.json()
            except ValueError:
                content = response.text

        return {
            "success": response.status_code < 400,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content": content,
            "url": str(response.url),
        }

    except httpx.TimeoutException:
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": f"Request timed out after {timeout} seconds",
            "url": url,
        }
    except httpx.HTTPError as e:
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": f"Request error: {e!s}",
            "url": url,
        }
