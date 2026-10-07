"""Shared GitHub HTTP helper with sane timeouts, retries, and rate-limit handling.

Existing reviewer publish calls use ``github_request`` instead of raw
``httpx2.AsyncClient`` calls. App authentication and repository access checks
use the typed async SDK in ``openswe.github.sdk``. This helper centralises:

- **Timeouts**: httpx2 defaults to 5 s which is too aggressive for paginated
  GitHub/GraphQL fetches.  The default here is 30 s read / 10 s connect.
- **Retries**: exponential backoff with jitter for retryable HTTP status codes
  and transport errors, gated by method idempotency to prevent duplicate writes.
  See ``github_request`` for the full retry matrix.
- **Rate-limit awareness**: respects ``Retry-After`` headers and detects
  GitHub secondary rate limits (403 with ``X-RateLimit-Remaining: 0`` or a
  "secondary rate limit" body message), backing off before retrying.
"""

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

import httpx2

if TYPE_CHECKING:
    from openswe.github.pull_request_status import PullRequestClient

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"
GITHUB_HEADERS_VERSION = "2022-11-28"

DEFAULT_TIMEOUT = httpx2.Timeout(30.0, connect=10.0, pool=5.0)
DEFAULT_MAX_RETRIES = 3
_PAGE_SIZE = 100

_ALWAYS_RETRYABLE_STATUS = frozenset({429, 503})
_IDEMPOTENT_RETRYABLE_STATUS = frozenset({502, 504})
_SECONDARY_RATE_LIMIT_MARKERS = ("secondary rate limit", "rate limit")
_RETRYABLE_TRANSPORT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

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


def _is_secondary_rate_limit(response: httpx2.Response) -> bool:
    if response.status_code != 403:
        return False
    if response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    body = (response.text or "").lower()
    return any(marker in body for marker in _SECONDARY_RATE_LIMIT_MARKERS)


def _is_retryable_response(response: httpx2.Response, method: str) -> bool:
    if response.status_code in _ALWAYS_RETRYABLE_STATUS:
        return True
    if response.status_code in _IDEMPOTENT_RETRYABLE_STATUS:
        return method.upper() in _RETRYABLE_TRANSPORT_METHODS
    return _is_secondary_rate_limit(response)


def _retry_after_seconds(response: httpx2.Response) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            return None
    return None


def _compute_backoff(response: httpx2.Response | None, attempt: int) -> float:
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
    timeout: httpx2.Timeout | float | None = None,
    headers: dict[str, str] | None = None,
) -> AsyncIterator[httpx2.AsyncClient]:
    """Yield an ``httpx2.AsyncClient`` with sane GitHub defaults.

    The token (when provided) is baked into the default headers so callers
    don't need to pass headers on every request.  A custom ``timeout`` can
    override the default 30 s / 10 s-connect timeout.
    """
    merged_headers: dict[str, str] = {}
    if token:
        merged_headers.update(github_headers(token))
    if headers:
        merged_headers.update(headers)
    async with httpx2.AsyncClient(
        headers=merged_headers or None,
        timeout=timeout or DEFAULT_TIMEOUT,
    ) as client:
        yield client


async def github_request(
    client: httpx2.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    **kwargs: Any,
) -> httpx2.Response:
    """Execute a single GitHub API request with retries and rate-limit handling.

    Returns the ``httpx2.Response`` for non-retryable status codes and for
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
        except (httpx2.TimeoutException, httpx2.TransportError) as exc:
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

    raise last_exc or httpx2.HTTPError("Max retries exceeded")


class GitHubSignInRequired(Exception):
    """The person has no usable GitHub authorization and must sign in again."""

    def __init__(self, login: str) -> None:
        super().__init__(f"GitHub sign-in required for {login}")
        self.login = login


class GitHubAppUnavailable(Exception):
    """No GitHub App installation token can be minted for the request."""


class GitHubClient:
    """GitHub's REST and GraphQL APIs over one HTTP client, with ``github_request``'s retries.

    Open one as the person or the App it acts for: ``as_user`` or ``as_app``.
    Every call raises ``httpx2.HTTPError`` when GitHub fails or refuses it, and
    ``ValueError`` when it answers with something other than the expected shape.
    """

    def __init__(
        self,
        http: httpx2.AsyncClient,
        *,
        reauthorize: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.http = http
        self._reauthorize = reauthorize

    @classmethod
    @asynccontextmanager
    async def connect(
        cls, *, token: str | None = None, timeout: httpx2.Timeout | float | None = None
    ) -> AsyncIterator[Self]:
        async with github_client(token=token, timeout=timeout) as http:
            yield cls(http)

    @classmethod
    @asynccontextmanager
    async def as_user(
        cls, login: str, *, timeout: httpx2.Timeout | float | None = None
    ) -> AsyncIterator[Self]:
        """Acts with ``login``'s own GitHub permissions; a rejected token is refreshed once.

        Raises ``GitHubSignInRequired`` when ``login`` has no usable authorization.
        """
        # profiles imports this module.
        from openswe.dashboard.profiles import get_valid_access_token

        token = await get_valid_access_token(login)
        if not token:
            raise GitHubSignInRequired(login)

        async def reauthorize() -> str:
            if token := await get_valid_access_token(login, force_refresh=True):
                return token
            raise GitHubSignInRequired(login)

        async with github_client(token=token, timeout=timeout) as http:
            yield cls(http, reauthorize=reauthorize)

    @classmethod
    @asynccontextmanager
    async def as_app(
        cls,
        owner: str | None = None,
        repo: str | None = None,
        *,
        timeout: httpx2.Timeout | float | None = None,
    ) -> AsyncIterator[Self]:
        """Acts as the Open SWE GitHub App: its installation on ``owner/repo``, else the default one.

        Raises ``GitHubAppUnavailable`` when no installation token can be minted.
        """
        # app imports this module.
        from openswe.github.app import (
            get_github_app_installation_id_for_repo,
            get_github_app_installation_token,
        )

        installation_id = None
        if owner is not None and repo is not None:
            installation_id = await get_github_app_installation_id_for_repo(owner, repo)
            if installation_id is None:
                raise GitHubAppUnavailable(f"no GitHub App installation on {owner}/{repo}")
        token = await get_github_app_installation_token(installation_id=installation_id)
        if not token:
            raise GitHubAppUnavailable("GitHub App token unavailable")
        async with github_client(token=token, timeout=timeout) as http:
            yield cls(http)

    def repo(self, owner: str, name: str) -> RepoClient:
        return RepoClient(self, owner, name)

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx2.Response:
        """``path`` is relative to the REST API root, or an absolute URL."""
        url = path if "://" in path else f"{GITHUB_API_BASE}/{path}"
        response = await github_request(self.http, method, url, **kwargs)
        if response.status_code == 401 and self._reauthorize is not None:
            self.http.headers.update(github_headers(await self._reauthorize()))
            response = await github_request(self.http, method, url, **kwargs)
        response.raise_for_status()
        return response

    async def get(self, path: str, params: Mapping[str, str] | None = None) -> object:
        return (await self.request("GET", path, params=params)).json()

    async def pages(
        self, path: str, *, key: str | None = None, params: Mapping[str, str] | None = None
    ) -> list[dict[str, Any]]:
        """Every item of a paginated list; ``key`` names the list inside each page's object."""
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self.get(
                path, {**(params or {}), "per_page": str(_PAGE_SIZE), "page": str(page)}
            )
            batch = (
                payload if key is None else payload.get(key) if isinstance(payload, dict) else None
            )
            if not isinstance(batch, list):
                raise ValueError(f"GitHub answered {path} without a list")
            items.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < _PAGE_SIZE:
                return items
            page += 1

    async def graphql(self, query: str, variables: Mapping[str, object]) -> Mapping[str, Any]:
        """The response's ``data``; GraphQL errors raise ``GraphQLError``."""
        payload = (
            await self.request(
                "POST", GITHUB_GRAPHQL, json={"query": query, "variables": dict(variables)}
            )
        ).json()
        if not isinstance(payload, Mapping):
            raise ValueError("GitHub answered GraphQL without an object")
        if payload.get("errors"):
            raise GraphQLError(payload["errors"])
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ValueError("GitHub answered GraphQL without data")
        return data


@dataclass(frozen=True, slots=True)
class RepoClient:
    """One repository's calls; REST paths are relative to ``repos/<owner>/<name>/``.

    Raises like ``GitHubClient``.
    """

    github: GitHubClient
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def pull_request(self, number: int) -> PullRequestClient:
        # pull_request_status imports this module.
        from openswe.github.pull_request_status import PullRequestClient

        return PullRequestClient(self, number)

    async def get(self, path: str, params: Mapping[str, str] | None = None) -> object:
        return await self.github.get(f"repos/{self.full_name}/{path}", params)

    async def pages(
        self, path: str, *, key: str | None = None, params: Mapping[str, str] | None = None
    ) -> list[dict[str, Any]]:
        return await self.github.pages(f"repos/{self.full_name}/{path}", key=key, params=params)

    async def graphql(
        self, query: str, variables: Mapping[str, object] | None = None
    ) -> Mapping[str, Any]:
        """``$owner`` and ``$repo`` are this repository's."""
        return await self.github.graphql(
            query, {"owner": self.owner, "repo": self.name, **(variables or {})}
        )

    async def check_runs(self, sha: str) -> list[dict[str, Any]]:
        """The latest run of each check on ``sha``."""
        return await self.pages(
            f"commits/{sha}/check-runs", key="check_runs", params={"filter": "latest"}
        )

    async def commit_statuses(self, sha: str) -> list[dict[str, Any]]:
        """The latest status per context on ``sha``."""
        latest: dict[str, dict[str, Any]] = {}
        for status in await self.pages(f"commits/{sha}/status", key="statuses"):
            context = status.get("context")
            if isinstance(context, str):
                latest.setdefault(context, status)
        return list(latest.values())


async def or_none[T](read: Awaitable[T]) -> T | None:
    """``read``'s answer, or ``None`` when GitHub could not give one."""
    try:
        return await read
    except httpx2.HTTPError, ValueError:
        return None


class GraphQLError(ValueError):
    """GitHub answered a GraphQL query with errors."""

    def __init__(self, errors: object) -> None:
        super().__init__(f"GitHub GraphQL errors: {errors}")
        self.errors = errors
