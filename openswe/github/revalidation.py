"""Conditional GitHub reads: a repeated GET asks whether its answer changed.

GitHub doesn't count a ``304 Not Modified`` against the token's primary rate limit,
so polling a pull request, its reviews, files or checks costs no quota until one of
them changes. Robin reads the same pull requests from every replica many times an
hour, which on its own used to exhaust the App installation's hourly quota.
"""

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass

import httpx2

from openswe.utils.http_pool import pooled_transport

_GITHUB_API_HOST = "api.github.com"
# Headers a 304 carries that are fresher than the kept answer's.
_REFRESHED_HEADERS = frozenset(
    {
        "date",
        "etag",
        "x-github-request-id",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "x-ratelimit-resource",
        "x-ratelimit-used",
    }
)


@dataclass(frozen=True, slots=True)
class _Kept:
    etag: str
    headers: httpx2.Headers
    # The body as it came over the wire, still encoded as ``headers`` say.
    raw: bytes


class RevalidatingTransport(httpx2.AsyncBaseTransport):
    """Sends a GitHub GET with the ETag of the last answer and replays that answer on a 304.

    Only authenticated GETs to the REST API take part. Answers are keyed by a hash of
    the request's ``Authorization`` header, its URL and the headers GitHub varies on,
    so a token never reads what another fetched. A replayed answer is a 200 whose
    reason phrase stays ``Not Modified``, so request logs still show what GitHub said.
    The least recently used answers go first once they exceed ``max_bytes``.
    """

    def __init__(
        self,
        inner: httpx2.AsyncBaseTransport,
        *,
        max_bytes: int = 64 * 1024 * 1024,
        max_entry_bytes: int = 1024 * 1024,
    ) -> None:
        self.inner = inner
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        # TODO: Keep these in Redis once Robin has one, so every replica shares them;
        # in process memory each replica still pays a full read before its first 304.
        self._kept: OrderedDict[str, _Kept] = OrderedDict()
        self._bytes = 0
        # Each isolated-loop worker thread sends through this one transport.
        self._lock = threading.Lock()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        key = self._key(request)
        if key is None:
            return await self.inner.handle_async_request(request)
        kept = self._get(key)
        if kept is not None:
            request.headers["If-None-Match"] = kept.etag
        response = await self.inner.handle_async_request(request)
        if response.status_code == 304 and kept is not None:
            await response.aclose()
            return self._replay(kept, response, request)
        etag = response.headers.get("ETag")
        if (
            response.status_code != 200
            or not etag
            or self._too_large(response)
            or not isinstance(response.stream, httpx2.AsyncByteStream)
        ):
            return response
        raw = b"".join([chunk async for chunk in response.stream])
        await response.aclose()
        if len(raw) <= self.max_entry_bytes:
            self._put(key, _Kept(etag, response.headers.copy(), raw))
        return httpx2.Response(
            200,
            headers=response.headers,
            content=raw,
            request=request,
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        # Every ``github_client`` shares this transport; closing one leaves it open.
        return None

    def clear(self) -> None:
        with self._lock:
            self._kept.clear()
            self._bytes = 0

    @staticmethod
    def _key(request: httpx2.Request) -> str | None:
        authorization = request.headers.get("Authorization")
        if (
            request.method != "GET"
            or request.url.host != _GITHUB_API_HOST
            or not authorization
            or "If-None-Match" in request.headers
            or "Range" in request.headers
        ):
            return None
        varies = "\0".join(
            (
                authorization,
                str(request.url),
                request.headers.get("Accept", ""),
                request.headers.get("X-GitHub-Api-Version", ""),
            )
        )
        return hashlib.sha256(varies.encode()).hexdigest()

    def _too_large(self, response: httpx2.Response) -> bool:
        """A body declared larger than an entry may be passes through still streaming."""
        try:
            return int(response.headers.get("Content-Length", "0")) > self.max_entry_bytes
        except ValueError:
            return True

    @staticmethod
    def _replay(
        kept: _Kept, not_modified: httpx2.Response, request: httpx2.Request
    ) -> httpx2.Response:
        headers = kept.headers.copy()
        for name, value in not_modified.headers.items():
            if name.lower() in _REFRESHED_HEADERS:
                headers[name] = value
        return httpx2.Response(
            200,
            headers=headers,
            content=kept.raw,
            request=request,
            extensions=not_modified.extensions,
        )

    def _get(self, key: str) -> _Kept | None:
        with self._lock:
            kept = self._kept.get(key)
            if kept is not None:
                self._kept.move_to_end(key)
            return kept

    def _put(self, key: str, kept: _Kept) -> None:
        with self._lock:
            if (previous := self._kept.pop(key, None)) is not None:
                self._bytes -= len(previous.raw)
            self._kept[key] = kept
            self._bytes += len(kept.raw)
            while self._bytes > self.max_bytes and self._kept:
                _, evicted = self._kept.popitem(last=False)
                self._bytes -= len(evicted.raw)


GITHUB_TRANSPORT = RevalidatingTransport(pooled_transport())
