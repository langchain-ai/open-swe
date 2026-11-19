"""Holds an App installation's requests while GitHub says its quota is spent.

Once GitHub answers an installation's request with ``x-ratelimit-remaining: 0``, it
refuses everything else that installation sends on that resource until
``x-ratelimit-reset``. Asking anyway wastes a round trip, logs another refusal, and
piles retries onto the reset. The gate answers those requests itself, with the
refusal GitHub would have sent, so callers handle it exactly as they handle GitHub's.

A secondary limit's ``Retry-After`` holds the installation's requests the same way.
Only requests whose installation token is tagged with its installation are held: a
person's token and the App's JWT spend their own quotas. Each replica holds its own
requests; until it learns of a spent quota it pays one refusal to find out.
"""

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import httpx2

from openswe.github.metering import HELD, INSTALLATION, MeteredTransport

logger = logging.getLogger(__name__)

_GITHUB_API_HOST: Final = "api.github.com"
# GitHub doesn't count reading the quotas against them, and the quota sampler must see
# the reset from inside a spent window.
_EXEMPT_PATHS: Final = frozenset({"/rate_limit"})
# A secondary limit is not per resource: it holds every request of the installation.
_SECONDARY: Final = "secondary"
# A reset GitHub puts further out than its hourly window is not trusted past it.
_MAX_HOLD: Final = 3600.0


@dataclass(slots=True)
class _Hold:
    until: float
    # Requests answered without asking GitHub.
    held: int = 0


class QuotaGate(httpx2.AsyncBaseTransport):
    """Answers an installation's requests itself while GitHub says its quota is spent."""

    def __init__(
        self, inner: httpx2.AsyncBaseTransport, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.inner = inner
        self._clock = clock
        self._holds: dict[tuple[int, str], _Hold] = {}
        # Each isolated-loop worker thread sends through this one transport.
        self._lock = threading.Lock()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        installation = self._installation(request)
        if installation is None:
            return await self.inner.handle_async_request(request)
        resource = self.resource(request.url)
        for key in ((installation, resource), (installation, _SECONDARY)):
            if (until := self._held_until(key)) is not None:
                return self._refusal(request, key, until)
        response = await self.inner.handle_async_request(request)
        self._observe(installation, response)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()

    @staticmethod
    def resource(url: httpx2.URL) -> str:
        """The quota GitHub charges a request to, as ``x-ratelimit-resource`` names it."""
        if url.path == "/graphql":
            return "graphql"
        if url.path.startswith("/search/"):
            return "search"
        return "core"

    @staticmethod
    def _installation(request: httpx2.Request) -> int | None:
        installation = request.extensions.get(INSTALLATION)
        if (
            request.url.host != _GITHUB_API_HOST
            or request.url.path in _EXEMPT_PATHS
            or not isinstance(installation, int)
            or MeteredTransport.token_kind(request.headers.get("Authorization")) != "installation"
        ):
            return None
        return installation

    def _held_until(self, key: tuple[int, str]) -> float | None:
        with self._lock:
            hold = self._holds.get(key)
            if hold is None:
                return None
            if self._clock() < hold.until:
                hold.held += 1
                return hold.until
            del self._holds[key]
        self._log_cleared(key, hold)
        return None

    def _observe(self, installation: int, response: httpx2.Response) -> None:
        headers = response.headers
        now = self._clock()
        resource = headers.get("x-ratelimit-resource")
        remaining = headers.get("x-ratelimit-remaining")
        reset = self._number(headers.get("x-ratelimit-reset"))
        if resource and remaining == "0" and reset is not None and reset > now:
            self._hold((installation, resource), min(reset, now + _MAX_HOLD))
        elif resource and remaining not in (None, "0") and reset is not None:
            # A later window than the one held has started, say after GitHub reset early.
            self._release((installation, resource), reset)
        if (
            response.status_code in (403, 429)
            and (retry_after := self._number(headers.get("retry-after"))) is not None
            and retry_after > 0
        ):
            self._hold((installation, _SECONDARY), now + min(retry_after, _MAX_HOLD))

    def _hold(self, key: tuple[int, str], until: float) -> None:
        with self._lock:
            if (hold := self._holds.get(key)) is not None:
                hold.until = max(hold.until, until)
                return
            self._holds[key] = _Hold(until)
        installation, resource = key
        logger.warning(
            "GitHub installation rate limit spent; holding its requests until reset",
            extra={
                "github_installation_id": installation,
                "github_quota_resource": resource,
                "github_quota_reset": math.ceil(until),
                "github_hold_seconds": round(until - self._clock(), 1),
            },
        )

    def _release(self, key: tuple[int, str], reset: float) -> None:
        with self._lock:
            hold = self._holds.get(key)
            if hold is None or reset <= hold.until:
                return
            del self._holds[key]
        self._log_cleared(key, hold)

    @staticmethod
    def _log_cleared(key: tuple[int, str], hold: _Hold) -> None:
        installation, resource = key
        logger.info(
            "GitHub installation rate limit reset; sending its requests again",
            extra={
                "github_installation_id": installation,
                "github_quota_resource": resource,
                "github_held_requests": hold.held,
            },
        )

    def _refusal(
        self, request: httpx2.Request, key: tuple[int, str], until: float
    ) -> httpx2.Response:
        """The refusal GitHub would send, so ``GitHubError`` and the retries classify it."""
        installation, resource = key
        reset = math.ceil(until)
        if resource == _SECONDARY:
            headers = {"Retry-After": str(max(math.ceil(until - self._clock()), 1))}
            message = "You have exceeded a secondary rate limit. Robin held this request."
        else:
            headers = {
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": str(reset),
                "x-ratelimit-resource": resource,
            }
            message = (
                f"API rate limit exceeded for installation ID {installation}. "
                "Robin held this request until the rate limit resets."
            )
        return httpx2.Response(
            403,
            headers={**headers, "Content-Type": "application/json"},
            content=json.dumps({"message": message}).encode(),
            request=request,
            extensions={HELD: True},
        )

    @staticmethod
    def _number(value: str | None) -> float | None:
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None
