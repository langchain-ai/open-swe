"""Requests the run sends to a host someone else named, only at a public address.

A URL from a catalog or a message may name any host. These requests go to an address
checked as public, so neither a redirect nor a second DNS answer can point them, or
the credential they carry, at a private host. ``PublicOrigin`` stays on the URL's own
HTTPS origin; ``request_public``, which the model's own requests take, follows
redirects to any public host, checking each and dropping the credentials a redirect
to another origin would carry, as the backend's ``openswe.utils.url_safety`` does. It
reads at most ``MAX_RESPONSE_BYTES`` of the answer, so a host serving a huge body
cannot exhaust the worker's memory.
"""

import asyncio
import ipaddress
import logging
import socket
from typing import Any, Final
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx2

logger = logging.getLogger(__name__)

_MAX_REDIRECTS: Final = 5
_REDIRECT_CODES: Final = frozenset({301, 302, 303, 307, 308})
_ENTITY_HEADERS: Final = frozenset(
    {"content-encoding", "content-language", "content-length", "content-type"}
)
_SENSITIVE_HEADERS: Final = frozenset({"authorization", "cookie", "proxy-authorization"})
# The most of an answer's body ``request_public`` reads; the rest is never downloaded.
MAX_RESPONSE_BYTES: Final = 10 * 1024 * 1024
# Set on a response whose body stopped at ``MAX_RESPONSE_BYTES``.
_TRUNCATED: Final = "open_swe_truncated"
# The body read is already decoded, so the headers that describe its encoding no longer do.
_DECODED_HEADERS: Final = frozenset({"content-encoding", "content-length", "transfer-encoding"})


class NotPublicError(ValueError):
    """A request the run refused to send; its message is ours."""


class BlockedUrlError(NotPublicError):
    """A URL ``request_public`` would not send to; ``url`` is the one it stopped at."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"Request blocked: {reason}")
        self.url = url


def _origin(url: str) -> tuple[str, str | None, int]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or 443


def _address(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """``address``, an IPv4-mapped one as the IPv4 address it maps; ValueError if not one."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


async def public_addresses(
    host: str, *, name: str, error: type[ValueError] = NotPublicError
) -> list[str]:
    """``host``'s addresses, once every one is public; ``error`` otherwise."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise error(f"{name} has no addresses")
    for address in addresses:
        try:
            ip = _address(address)
        except ValueError:
            raise error(f"{name} must resolve only to public addresses") from None
        if not ip.is_global:
            raise error(f"{name} must resolve only to public addresses")
    return addresses


async def _checked(url: str) -> tuple[str, list[str]]:
    """``url``'s host and its addresses, once every one is public; ``BlockedUrlError`` otherwise."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise BlockedUrlError(url, f"Unsupported URL scheme: {parsed.scheme or '<missing>'}")
        hostname = parsed.hostname
        if not hostname:
            raise BlockedUrlError(url, "Could not parse hostname from URL")
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(hostname, None)
        except socket.gaierror as exc:
            raise BlockedUrlError(url, f"Could not resolve hostname: {hostname}") from exc
        if not infos:
            raise BlockedUrlError(url, f"Could not resolve hostname: {hostname}")
        for info in infos:
            address = str(info[4][0])
            try:
                ip = _address(address)
            except ValueError as exc:
                raise BlockedUrlError(url, f"Could not parse resolved address: {address}") from exc
            if not ip.is_global:
                raise BlockedUrlError(url, f"URL resolves to blocked address: {address}")
        return hostname, list(dict.fromkeys(str(info[4][0]) for info in infos))
    except BlockedUrlError:
        raise
    except Exception as exc:
        raise BlockedUrlError(url, f"URL validation error: {exc}") from exc


def _pinned(url: str, address: str) -> str:
    parsed = urlsplit(url)
    host = f"[{address}]" if ":" in address else address
    return urlunsplit(parsed._replace(netloc=f"{host}:{parsed.port}" if parsed.port else host))


def _any_origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        port = -1
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _sensitive(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in _SENSITIVE_HEADERS or "api-key" in normalized or normalized.endswith("-token")
    )


def _redirect_method(method: str, status_code: int) -> str:
    if status_code in {302, 303} and method != "HEAD":
        return "GET"
    if status_code == 301 and method == "POST":
        return "GET"
    return method


def truncated(response: httpx2.Response) -> bool:
    """Whether ``request_public`` stopped reading ``response``'s body at its limit."""
    return bool(response.extensions.get(_TRUNCATED))


async def _capped(response: httpx2.Response) -> httpx2.Response:
    """``response`` with at most ``MAX_RESPONSE_BYTES`` of its body read, then closed."""
    body = bytearray()
    cut = False
    try:
        async for chunk in response.aiter_bytes():
            room = MAX_RESPONSE_BYTES - len(body)
            if len(chunk) > room:
                body.extend(chunk[:room])
                cut = True
                break
            body.extend(chunk)
    finally:
        await response.aclose()
    return httpx2.Response(
        response.status_code,
        headers=[
            (name, value)
            for name, value in response.headers.multi_items()
            if name.lower() not in _DECODED_HEADERS
        ],
        content=bytes(body),
        request=response.request,
        extensions={**response.extensions, _TRUNCATED: cut},
    )


async def request_public(
    client: httpx2.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx2.Response:
    """``client``'s request, at a public address checked for each host it is redirected to.

    A redirect to another origin drops the credentials the request carried, and one that
    changes its method to ``GET`` drops its body. At most ``MAX_RESPONSE_BYTES`` of the
    answer's body is read (``truncated`` says whether it was cut). Raises
    ``BlockedUrlError``.
    """
    current_method = method.upper()
    current_url = url
    request_kwargs: dict[str, Any] = dict(kwargs)
    raw_headers = request_kwargs.pop("headers", None)
    headers_sent = dict(raw_headers) if isinstance(raw_headers, dict) else {}
    for redirect_count in range(_MAX_REDIRECTS + 1):
        hostname, addresses = await _checked(current_url)
        headers = {**headers_sent, "Host": urlsplit(current_url).netloc}
        response: httpx2.Response | None = None
        for index, address in enumerate(addresses):
            try:
                request = client.build_request(
                    current_method,
                    _pinned(current_url, address),
                    headers=headers,
                    extensions={"sni_hostname": hostname},
                    **{key: value for key, value in request_kwargs.items() if key != "auth"},
                )
                response = await client.send(
                    request,
                    stream=True,
                    follow_redirects=False,
                    **({"auth": request_kwargs["auth"]} if "auth" in request_kwargs else {}),
                )
                break
            except httpx2.ConnectError, httpx2.ConnectTimeout:
                if index == len(addresses) - 1:
                    raise
        if response is None:
            raise httpx2.ConnectError("No response received from pinned address")
        location = response.headers.get("Location")
        if response.status_code not in _REDIRECT_CODES or not location:
            return await _capped(response)
        await response.aclose()
        if redirect_count == _MAX_REDIRECTS:
            raise BlockedUrlError(current_url, "Too many redirects")
        next_url = urljoin(current_url, location)
        request_kwargs.pop("params", None)
        if _any_origin(current_url) != _any_origin(next_url):
            headers_sent = {k: v for k, v in headers_sent.items() if not _sensitive(k)}
            request_kwargs.pop("auth", None)
            request_kwargs.pop("cookies", None)
        next_method = _redirect_method(current_method, response.status_code)
        if next_method != current_method:
            headers_sent = {
                k: v for k, v in headers_sent.items() if k.lower() not in _ENTITY_HEADERS
            }
            for body in ("data", "content", "json"):
                request_kwargs.pop(body, None)
        current_method = next_method
        current_url = next_url
    raise BlockedUrlError(current_url, "Too many redirects")


class PublicOrigin(httpx2.AsyncBaseTransport):
    """Reaches only ``url``'s own HTTPS origin, at an address checked as public.

    The request goes to the checked address, so a second DNS answer cannot point it
    at a private host. ``name`` says what the host is in ``error``'s message.
    """

    def __init__(self, url: str, *, name: str, error: type[ValueError] = NotPublicError) -> None:
        self._origin = _origin(url)
        self._name = name
        self._error = error
        self._transport = httpx2.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or _origin(url) != self._origin or not parsed.hostname:
            raise self._error(f"requests to the {self._name} must stay on its HTTPS origin")
        headers = request.headers.copy()
        headers["Host"] = parsed.netloc
        addresses = await public_addresses(parsed.hostname, name=self._name, error=self._error)
        for index, address in enumerate(addresses):
            host = f"[{address}]" if ":" in address else address
            pinned = httpx2.Request(
                request.method,
                urlunsplit(parsed._replace(netloc=f"{host}:{parsed.port or 443}")),
                headers=headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": parsed.hostname},
            )
            try:
                return await self._transport.handle_async_request(pinned)
            except (httpx2.ConnectError, httpx2.ConnectTimeout) as exc:
                if index == len(addresses) - 1:
                    raise
                logger.info(
                    "Trying a host's next address",
                    extra={
                        "public_host": parsed.hostname,
                        "host_kind": self._name,
                        "error_type": type(exc).__name__,
                    },
                )
        raise httpx2.ConnectError(f"{self._name} has no public addresses")

    async def aclose(self) -> None:
        await self._transport.aclose()
