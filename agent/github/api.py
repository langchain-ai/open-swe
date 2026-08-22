"""The one place GitHub is spoken to over HTTP.

Every GitHub API call in ``agent/`` builds its URL with :func:`github_url`, its
headers with :func:`github_headers`, and issues the request through
:func:`github_request` (or :func:`github_paginate` / :func:`github_graphql`)
rather than a raw ``httpx.AsyncClient``. That keeps four cross-cutting concerns
in a single module:

- **Timeouts**: httpx defaults to 5 s which is too aggressive for paginated
  GitHub/GraphQL fetches.  The default here is 30 s read / 10 s connect.
- **Retries**: exponential backoff with jitter for retryable HTTP status codes
  and transport errors, gated by method idempotency to prevent duplicate writes.
  See ``github_request`` for the full retry matrix.
- **Rate-limit awareness**: respects ``Retry-After`` headers and detects
  GitHub secondary rate limits (403 with ``X-RateLimit-Remaining: 0`` or a
  "secondary rate limit" body message), backing off before retrying.
- **Host and media types**: the API host and the ``Accept``/API-version headers
  are spelled once, so a deployment can repoint them (the E2E harness does) and
  no caller can drift onto an unversioned or unauthenticated request.

``tests/agent/test_github_http_centralization.py`` pins that centralisation.
"""

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL_PATH = "/graphql"
GITHUB_GRAPHQL = f"{GITHUB_API_BASE}{GITHUB_GRAPHQL_PATH}"
GITHUB_HEADERS_VERSION = "2022-11-28"

GITHUB_JSON_ACCEPT = "application/vnd.github+json"
GITHUB_DIFF_ACCEPT = "application/vnd.github.diff"
GITHUB_RAW_ACCEPT = "application/vnd.github.raw"
GITHUB_RAW_JSON_ACCEPT = "application/vnd.github.raw+json"
GITHUB_TEXT_MATCH_ACCEPT = "application/vnd.github.text-match+json"

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0, pool=5.0)
DEFAULT_MAX_RETRIES = 3
DEFAULT_PER_PAGE = 100

_ALWAYS_RETRYABLE_STATUS = frozenset({429, 503})
_IDEMPOTENT_RETRYABLE_STATUS = frozenset({502, 504})
_SECONDARY_RATE_LIMIT_MARKERS = ("secondary rate limit", "rate limit")
_RETRYABLE_TRANSPORT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

_BASE_BACKOFF = 1.0
_BACKOFF_MULTIPLIER = 2.0
_MAX_BACKOFF = 60.0
_JITTER_FACTOR = 0.25


def github_url(path: str) -> str:
    """Absolute URL for an API path such as ``/repos/{owner}/{repo}/pulls``."""
    return f"{GITHUB_API_BASE}{path}"


def github_headers(token: str | None = None, *, accept: str = GITHUB_JSON_ACCEPT) -> dict[str, str]:
    """Standard GitHub API headers, authenticated when a token is given."""
    headers = {"Accept": accept, "X-GitHub-Api-Version": GITHUB_HEADERS_VERSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_error_message(response: httpx.Response) -> str:
    """GitHub's own error text for a failed response, for surfacing to callers.

    Falls back to the raw body and finally to the status code, so the result is
    always something a human can act on.
    """
    fallback = response.text.strip() or f"HTTP {response.status_code}"
    try:
        data = response.json()
    except ValueError:
        return fallback
    if not isinstance(data, dict):
        return fallback
    message = data.get("message")
    message_str = message.strip() if isinstance(message, str) else ""
    errors = data.get("errors")
    details = [
        err["message"]
        for err in (errors if isinstance(errors, list) else [])
        if isinstance(err, dict) and isinstance(err.get("message"), str)
    ]
    detail = "; ".join(details)
    if message_str and detail:
        return f"{message_str}: {detail}"
    return message_str or detail or fallback


def _is_secondary_rate_limit(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    if response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    body = (response.text or "").lower()
    return any(marker in body for marker in _SECONDARY_RATE_LIMIT_MARKERS)


def _is_retryable_response(response: httpx.Response, method: str) -> bool:
    if response.status_code in _ALWAYS_RETRYABLE_STATUS:
        return True
    if response.status_code in _IDEMPOTENT_RETRYABLE_STATUS:
        return method.upper() in _RETRYABLE_TRANSPORT_METHODS
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
    accept: str = GITHUB_JSON_ACCEPT,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an ``httpx.AsyncClient`` with sane GitHub defaults.

    The token and media type are baked into the default headers so callers
    don't need to pass headers on every request.  A custom ``timeout`` can
    override the default 30 s / 10 s-connect timeout.
    """
    merged_headers = github_headers(token, accept=accept)
    if headers:
        merged_headers.update(headers)
    async with httpx.AsyncClient(
        headers=merged_headers,
        timeout=timeout or DEFAULT_TIMEOUT,
        follow_redirects=follow_redirects,
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

    Retry matrix:

    | Condition                         | Idempotent (GET, PUT, DELETE…) | Non-idempotent (POST, PATCH) |
    |-----------------------------------|--------------------------------|------------------------------|
    | Transport error (timeout/reset)   | Retry with backoff             | Raise immediately            |
    | 429 / 503 / secondary rate limit  | Retry with backoff             | Retry with backoff           |
    | 502 / 504 (ambiguous gateway)     | Retry with backoff             | Raise immediately            |

    429 and 503 are safe to retry for any method: the server explicitly did
    not process the request.  502/504 are ambiguous — the upstream may have
    processed the write before the gateway returned an error — so they are
    only retried for idempotent methods.  Transport errors are only retried
    for idempotent methods for the same reason.
    """
    method_upper = method.upper()
    retry_transport = method_upper in _RETRYABLE_TRANSPORT_METHODS
    method_func = getattr(client, method.lower())
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = await method_func(url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if retry_transport and attempt < max_retries:
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

        if _is_retryable_response(response, method):
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


def next_page_url(link_header: str | None) -> str | None:
    """The ``rel="next"`` target of a GitHub ``Link`` header, if there is one."""
    if not link_header:
        return None
    # '<url>; rel="next", <url>; rel="last"'
    for part in link_header.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) >= 2 and 'rel="next"' in segments[1] and segments[0].startswith("<"):
            return segments[0][1:-1]
    return None


async def github_paginate(
    client: httpx.AsyncClient,
    url: str,
    *,
    items_key: str | None = None,
    cap: int = 1000,
    per_page: int = DEFAULT_PER_PAGE,
    params: dict[str, Any] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    **kwargs: Any,
) -> list[Any]:
    """Follow ``Link: rel="next"`` until exhausted or ``cap`` items are collected.

    ``items_key`` names the JSON key holding the list when the endpoint wraps it
    in an object (``/user/installations`` returns ``{"total_count": N,
    "installations": [...]}``); when ``None`` the body itself is the list. Raises
    ``httpx.HTTPStatusError`` on the first failing page — callers that need
    partial results or a bespoke error mapping wrap this.
    """
    out: list[Any] = []
    next_url: str | None = url
    page_params: dict[str, Any] | None = {"per_page": str(per_page), **(params or {})}
    while next_url and len(out) < cap:
        response = await github_request(
            client, "GET", next_url, params=page_params, max_retries=max_retries, **kwargs
        )
        response.raise_for_status()
        body = response.json()
        page = body.get(items_key, []) if items_key and isinstance(body, dict) else body
        if isinstance(page, list):
            out.extend(page)
        next_url = next_page_url(response.headers.get("Link"))
        # The Link URL already carries the cursor and page size.
        page_params = None
    return out


async def github_graphql(
    client: httpx.AsyncClient,
    query: str,
    *,
    variables: dict[str, Any] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """POST a GraphQL query to GitHub's ``/graphql`` endpoint."""
    return await github_request(
        client,
        "POST",
        github_url(GITHUB_GRAPHQL_PATH),
        json={"query": query, "variables": variables or {}},
        **kwargs,
    )
