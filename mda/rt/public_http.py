"""Requests the run sends to a host someone else named, only at a public address.

A URL from a catalog or a message may name any host. These requests stay on the
URL's own HTTPS origin and go to an address checked as public, so neither a
redirect nor a second DNS answer can point them, or the credential they carry, at
a private host.
"""

import asyncio
import ipaddress
import logging
from urllib.parse import urlsplit, urlunsplit

import httpx2

logger = logging.getLogger(__name__)


class NotPublicError(ValueError):
    """A request the run refused to send; its message is ours."""


def _origin(url: str) -> tuple[str, str | None, int]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or 443


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
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise error(f"{name} must resolve only to public addresses") from None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise error(f"{name} must resolve only to public addresses")
    return addresses


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
