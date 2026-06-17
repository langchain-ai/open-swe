"""Shared GitHub HTTP helper with sane timeouts, retries, and rate-limit handling.

All GitHub API calls in the reviewer publish path (and gradually everywhere else)
should go through ``github_request`` instead of raw ``httpx.AsyncClient`` calls.
This centralises:

- **Timeouts**: httpx defaults to 5 s which is too aggressive for paginated
  GitHub/GraphQL fetches.  The default here is 30 s read / 10 s connect.
- **Retries**: exponential backoff with jitter for transient transport errors
  (timeouts, connection resets) and retryable HTTP status codes (429, 502,
  503, 504).
- **Rate-limit awareness**: respects ``Retry-After`` headers and detects
  GitHub secondary rate limits (403 with ``X-RateLimit-Remaining: 0`` or a
  "secondary rate limit" body message), backing off before retrying.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"
GITHUB_HEADERS_VERSION = "2022-11-28"

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0, pool=5.0)
DEFAULT_MAX_RETRIES = 3

_RETRY_STATUS_CODES = frozenset({429, 502, 503, 504})
_SECONDARY_RATE_LIMIT_MARKERS = ("secondary rate limit", "rate limit")

_BASE_BACKOFF = 1.0
_BACKOFF_MULTIPLIER = 2.0
_MAX_BACKOFF = 60.0
_JITTER_FACTOR = 0.25


def github_headers(token: str) -> dict[str, str]:
    """Standard GitHub API headers for a bearer token."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_HEADERS_VERSION,
    }


def _is_secondary_rate_limit(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    if response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    body = (response.text or "").lower()
    return any(marker in body for marker in _SECONDARY_RATE_LIMIT_MARKERS)


def _is_retryable_response(response: httpx.Response) -> bool:
    if response.status_code in _RETRY_STATUS_CODES:
        return True
    return _is_secondary_rate_limit(response)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            return None
    return None


def _compute_backoff(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            return min(retry_after, _MAX_BACKOFF)
    base = _BASE_BACKOFF * (_BACKOFF_MULTIPLIER**attempt)
    jitter = base * random.uniform(-_JITTER_FACTOR, _JITTER_FACTOR)
    return min(base + jitter, _MAX_BACKOFF)


@asynccontextmanager
async def github_client(
    *,
    token: str | None = None,
    timeout: httpx.Timeout | float | None = None,
    headers: dict[str, str] | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an ``httpx.AsyncClient`` with sane GitHub defaults.

    The token (when provided) is baked into the default headers so callers
    don't need to pass headers on every request.  A custom ``timeout`` can
    override the default 30 s / 10 s-connect timeout.
    """
    merged_headers: dict[str, str] = {}
    if token:
        merged_headers.update(github_headers(token))
    if headers:
        merged_headers.update(headers)
    async with httpx.AsyncClient(
        headers=merged_headers or None,
        timeout=timeout or DEFAULT_TIMEOUT,
    ) as client:
        yield client


async def github_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    **kwargs: Any,
) -> httpx.Response:
    """Execute a single GitHub API request with retries and rate-limit handling.

    Returns the ``httpx.Response`` for non-retryable status codes and for
    retryable status codes that have exhausted retries (caller should call
    ``raise_for_status()``).

    Re-raises the last ``httpx.TimeoutException`` / ``httpx.TransportError``
    if all retries are exhausted on a transport-level failure.
    """
    method_func = getattr(client, method.lower())
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = await method_func(url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = _compute_backoff(None, attempt)
                logger.warning(
                    "GitHub API %s %s raised %s, retrying in %.1fs (attempt %d/%d)",
                    method,
                    url,
                    type(exc).__name__,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
                continue
            raise

        if _is_retryable_response(response):
            if attempt < max_retries:
                delay = _compute_backoff(response, attempt)
                logger.warning(
                    "GitHub API %s %s returned %d, retrying in %.1fs (attempt %d/%d)",
                    method,
                    url,
                    response.status_code,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
                continue
            logger.warning(
                "GitHub API %s %s returned %d after %d retries, giving up",
                method,
                url,
                response.status_code,
                max_retries,
            )
        return response

    raise last_exc or httpx.HTTPError("Max retries exceeded")
