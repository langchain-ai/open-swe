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
import io
import logging
import random
import re
import zipfile
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import quote, urlparse

import httpx2
from pydantic import BaseModel, JsonValue, TypeAdapter

from openswe.github.queue import (
    Artifact,
    CommitComparison,
    GitReference,
    ValidationArtifacts,
    ValidationJob,
    ValidationJobs,
    ValidationRun,
    ValidationRuns,
    WorkflowRun,
)
from openswe.utils.http_pool import pooled_transport
from openswe.utils.url_safety import request_with_safe_redirects

if TYPE_CHECKING:
    from openswe.github.pull_request_status import PullRequestClient
    from openswe.merge_queue.git import CandidateGit

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"
GITHUB_HEADERS_VERSION = "2022-11-28"

DEFAULT_TIMEOUT = httpx2.Timeout(30.0, connect=10.0, pool=5.0)
DEFAULT_MAX_RETRIES = 3
_PAGE_SIZE = 100
_PAGE_CONCURRENCY = 10
_FILES_PER_QUERY = 100

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
        transport=pooled_transport(),
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
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = await client.request(method_upper, url, **kwargs)
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


class RepoFileUnreadableError(RuntimeError):
    """A repository file could not be read reliably."""


class GitHubClient:
    """GitHub's REST and GraphQL APIs over one HTTP client, with ``github_request``'s retries.

    Open one as the person or the App it acts for: ``as_user`` or ``as_app``.
    Every call raises ``GitHubError`` when GitHub refuses it, another
    ``httpx2.HTTPError`` when it cannot be reached, and ``ValueError`` when it
    answers with something other than the expected shape.
    """

    def __init__(self, http: httpx2.AsyncClient, *, login: str | None = None) -> None:
        """``login`` is the person whose OAuth token ``http`` carries, if it is one."""
        self.http = http
        self.login = login
        self._refresh: asyncio.Future[str] | None = None

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

        Raises ``GitHubSignInRequired`` when ``login`` has no usable authorization,
        including when GitHub still rejects the refreshed token.
        """
        # profiles imports this module.
        from openswe.dashboard.profiles import get_valid_access_token

        token = await get_valid_access_token(login)
        if not token:
            raise GitHubSignInRequired(login)
        async with github_client(token=token, timeout=timeout) as http:
            yield cls(http, login=login)

    async def _refreshed_token(self, login: str) -> str:
        """One refresh per client, shared by every request that GitHub rejected."""
        # profiles imports this module.
        from openswe.dashboard.profiles import get_valid_access_token

        async def refresh() -> str:
            if token := await get_valid_access_token(login, force_refresh=True):
                return token
            raise GitHubSignInRequired(login)

        if self._refresh is None:
            self._refresh = asyncio.ensure_future(refresh())
        return await asyncio.shield(self._refresh)

    @classmethod
    @asynccontextmanager
    async def as_app(
        cls,
        owner: str | None = None,
        repo: str | None = None,
        *,
        installation_id: int | None = None,
        permissions: Mapping[str, str] | None = None,
        timeout: httpx2.Timeout | float | None = None,
    ) -> AsyncIterator[Self]:
        """Acts as the Open SWE GitHub App: ``installation_id``, else its installation on
        ``owner/repo``, else the default installation.

        ``permissions`` narrows the token to them, and to ``repo`` alone.
        Raises ``GitHubAppUnavailable`` when no installation token can be minted.
        """
        # app imports this module.
        from openswe.github.app import (
            get_github_app_installation_id_for_repo,
            get_github_app_installation_token,
        )

        if installation_id is None and owner is not None and repo is not None:
            installation_id = await get_github_app_installation_id_for_repo(owner, repo)
            if installation_id is None:
                raise GitHubAppUnavailable(f"no GitHub App installation on {owner}/{repo}")
        token = await get_github_app_installation_token(
            installation_id=installation_id,
            repositories=[repo] if permissions is not None and repo is not None else None,
            permissions=permissions,
        )
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
        if response.status_code == 401 and self.login is not None:
            self.http.headers.update(github_headers(await self._refreshed_token(self.login)))
            response = await github_request(self.http, method, url, **kwargs)
            if response.status_code == 401:
                raise GitHubSignInRequired(self.login)
        if not response.is_success:
            raise GitHubError(response)
        return response

    async def get(self, path: str, params: Mapping[str, str] | None = None) -> object:
        return (await self.request("GET", path, params=params)).json()

    async def post(self, path: str, json: Mapping[str, object]) -> object:
        return _json_or_none(await self.request("POST", path, json=dict(json)))

    async def patch(self, path: str, json: Mapping[str, object]) -> object:
        return _json_or_none(await self.request("PATCH", path, json=dict(json)))

    async def delete(self, path: str, json: Mapping[str, object] | None = None) -> None:
        await self.request("DELETE", path, **({} if json is None else {"json": dict(json)}))

    async def pages(
        self,
        path: str,
        *,
        key: str | None = None,
        params: Mapping[str, str] | None = None,
        max_pages: int | None = None,
        total: int | None = None,
    ) -> list[dict[str, Any]]:
        """Every item of a paginated list; ``key`` names the list inside each page's object.

        ``total`` is how many items the list should hold: the pages that many fill
        are read concurrently, and reading goes on while the last one comes back full.
        """

        async def read(page: int) -> list[object]:
            payload = await self.get(
                path, {**(params or {}), "per_page": str(_PAGE_SIZE), "page": str(page)}
            )
            batch = (
                payload if key is None else payload.get(key) if isinstance(payload, dict) else None
            )
            if not isinstance(batch, list):
                raise ValueError(f"GitHub answered {path} without a list")
            return batch

        semaphore = asyncio.Semaphore(_PAGE_CONCURRENCY)

        async def bounded(page: int) -> list[object]:
            async with semaphore:
                return await read(page)

        known = max(1, -(-(total or 0) // _PAGE_SIZE))
        if max_pages is not None:
            known = min(known, max_pages)
        batches = await asyncio.gather(*(bounded(page) for page in range(1, known + 1)))
        page = known
        while len(batches[-1]) == _PAGE_SIZE and page != max_pages:
            page += 1
            batches.append(await read(page))
        return [item for batch in batches for item in batch if isinstance(item, dict)]

    async def graphql(
        self, query: str, variables: Mapping[str, object], *, partial: bool = False
    ) -> Mapping[str, Any]:
        """The response's ``data``; GraphQL errors raise ``GraphQLError`` unless ``partial``.

        With ``partial``, fields GitHub could not resolve come back null beside the ones it did.
        """
        payload = (
            await self.request(
                "POST", GITHUB_GRAPHQL, json={"query": query, "variables": dict(variables)}
            )
        ).json()
        if not isinstance(payload, Mapping):
            raise ValueError("GitHub answered GraphQL without an object")
        if payload.get("errors") and not partial:
            raise GraphQLError(payload["errors"])
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ValueError("GitHub answered GraphQL without data")
        return data


@dataclass(frozen=True, slots=True)
class Comparison:
    """A three-dot ``base...head`` comparison."""

    merge_base: str
    files: list[dict[str, Any]]


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

    async def post(self, path: str, json: Mapping[str, object]) -> object:
        return await self.github.post(f"repos/{self.full_name}/{path}", json)

    async def patch(self, path: str, json: Mapping[str, object]) -> object:
        return await self.github.patch(f"repos/{self.full_name}/{path}", json)

    async def delete(self, path: str, json: Mapping[str, object] | None = None) -> None:
        await self.github.delete(f"repos/{self.full_name}/{path}", json)

    async def pages(
        self,
        path: str,
        *,
        key: str | None = None,
        params: Mapping[str, str] | None = None,
        max_pages: int | None = None,
        total: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self.github.pages(
            f"repos/{self.full_name}/{path}",
            key=key,
            params=params,
            max_pages=max_pages,
            total=total,
        )

    async def graphql(
        self, query: str, variables: Mapping[str, object] | None = None
    ) -> Mapping[str, Any]:
        """``$owner`` and ``$repo`` are this repository's."""
        return await self.github.graphql(
            query, {"owner": self.owner, "repo": self.name, **(variables or {})}
        )

    async def read_file(
        self, path: str, ref: str | None, *, max_chars: int, strict: bool = False
    ) -> str | None:
        """``path`` at ``ref`` (the default branch when ``None``); ``None`` when it is absent.

        Without ``strict`` an unreadable or oversized file also reads as ``None``; with it,
        those raise ``RepoFileUnreadableError``.
        """
        extra = {"repository": self.full_name, "ref": ref or "", "path": path}
        try:
            response = await self.github.request(
                "GET",
                f"repos/{self.full_name}/contents/{path}",
                params={"ref": ref} if ref else None,
                headers={"Accept": "application/vnd.github.raw"},
            )
        except GitHubError as refused:
            if refused.response.status_code == 404:
                return None
            logger.warning(
                "repository file fetch returned an unexpected status",
                extra={**extra, "status_code": refused.response.status_code},
            )
            if strict:
                raise RepoFileUnreadableError(
                    f"Could not read {path}: HTTP {refused.response.status_code}"
                ) from refused
            return None
        except httpx2.HTTPError as exc:
            logger.exception("repository file fetch failed", extra=extra)
            if strict:
                raise RepoFileUnreadableError(f"Could not read {path}") from exc
            return None
        content = response.text.strip()
        if len(content) > max_chars:
            logger.warning(
                "repository file exceeds the size cap; ignoring it",
                extra={**extra, "chars": len(content), "max_chars": max_chars},
            )
            if strict:
                raise RepoFileUnreadableError(f"Repository file {path} exceeds the size cap")
            return None
        return content if strict else content or None

    async def raw_file(self, path: str, ref: str) -> bytes | None:
        """``path``'s bytes at ``ref``, unaltered; ``None`` when it is absent there."""
        try:
            response = await self.github.request(
                "GET",
                f"repos/{self.full_name}/contents/{quote(path, safe='/')}",
                params={"ref": ref},
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except GitHubError as refused:
            if refused.response.status_code == 404:
                return None
            raise
        return response.content

    async def read_files(self, paths: Sequence[str], ref: str) -> dict[str, str]:
        """Text of each of ``paths`` present at ``ref``, read in batched GraphQL queries."""
        found: dict[str, str] = {}
        for start in range(0, len(paths), _FILES_PER_QUERY):
            batch = paths[start : start + _FILES_PER_QUERY]
            params = "".join(f",$e{i}:String!" for i in range(len(batch)))
            fields = "".join(
                f"f{i}:object(expression:$e{i}){{...on Blob{{text}}}}" for i in range(len(batch))
            )
            data = await self.graphql(
                f"query($owner:String!,$repo:String!{params})"
                f"{{repository(owner:$owner,name:$repo){{{fields}}}}}",
                {f"e{i}": f"{ref}:{path}" for i, path in enumerate(batch)},
            )
            repository = data.get("repository") or {}
            for i, path in enumerate(batch):
                blob = repository.get(f"f{i}")
                if isinstance(blob, Mapping) and isinstance(blob.get("text"), str):
                    found[path] = blob["text"]
        return found

    async def can_write(self, login: str) -> bool:
        """Whether ``login`` has write, maintain or admin here; any failure denies."""
        if not login:
            return False
        try:
            payload = await self.get(f"collaborators/{quote(login, safe='')}/permission")
        except httpx2.HTTPError, ValueError:
            logger.info(
                "Could not verify repository permission; denying",
                extra={"repo_full_name": self.full_name, "github_login": login},
            )
            return False
        permission = payload.get("permission") if isinstance(payload, dict) else None
        return permission in {"admin", "maintain", "write"}

    async def open_pull_for_branch(self, branch: str) -> dict[str, Any] | None:
        """The first open pull request whose head is ``branch`` in this repository."""
        try:
            payload = await self.get(
                "pulls", {"head": f"{self.owner}:{branch}", "state": "open", "per_page": "1"}
            )
        except httpx2.HTTPError, ValueError:
            logger.warning(
                "Failed to find open PR for branch",
                extra={"repo_full_name": self.full_name, "branch": branch},
            )
            return None
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        return None

    async def info(self) -> dict[str, Any]:
        """The repository itself: default branch, merge settings, visibility."""
        payload = await self.github.get(f"repos/{self.full_name}")
        if not isinstance(payload, dict):
            raise ValueError("GitHub answered the repository without an object")
        return payload

    async def labels(self) -> list[dict[str, Any]]:
        return await self.pages("labels")

    async def compare(self, base: str, head: str) -> Comparison:
        """``base...head``, with at most GitHub's 300 changed files; refs are fully escaped."""
        # The paginated commit list is never read, so only one commit comes back.
        payload = await self.get(
            f"compare/{quote(base, safe='')}...{quote(head, safe='')}", {"per_page": "1"}
        )
        if not isinstance(payload, dict):
            raise ValueError("GitHub answered the comparison without an object")
        merge_base = payload.get("merge_base_commit")
        sha = merge_base.get("sha") if isinstance(merge_base, dict) else None
        files = payload.get("files") or []
        if not isinstance(sha, str) or not isinstance(files, list):
            raise ValueError("GitHub answered the comparison without a merge base and files")
        return Comparison(sha, [file for file in files if isinstance(file, dict)])

    async def branch(self, name: str) -> dict[str, Any]:
        payload = await self.get(f"branches/{quote(name, safe='')}")
        if not isinstance(payload, dict):
            raise ValueError("GitHub answered the branch without an object")
        return payload

    async def branch_sha(self, name: str) -> str:
        payload = await self.branch(name)
        commit = payload.get("commit")
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(sha, str) or not sha:
            raise ValueError("Target branch is unavailable")
        return sha

    async def ref_sha(self, ref: str) -> str | None:
        """Read an exact qualified ref; API failures must not look like a missing ref."""
        if not ref.startswith("refs/"):
            raise ValueError("Expected a fully qualified Git ref")
        references = TypeAdapter(list[GitReference]).validate_python(
            await self.get(f"git/matching-refs/{quote(ref.removeprefix('refs/'), safe='')}")
        )
        return next((item.sha for item in references if item.ref == ref), None)

    async def branch_rules(self, name: str) -> list[dict[str, Any]]:
        """The rulesets' rules that apply to branch ``name``."""
        return await self.pages(f"rules/branches/{quote(name, safe='')}")

    async def commits(self, *, path: str, since: str, ref: str | None = None) -> object:
        """The first 100 commits since ``since`` that touched ``path``, on ``ref`` or the default."""
        params = {"path": path, "since": since, "per_page": "100"}
        if ref:
            params["sha"] = ref
        return await self.get("commits", params)

    async def delete_review_comment(self, comment_id: int) -> None:
        await self.delete(f"pulls/comments/{comment_id}")

    async def check_runs(self, sha: str) -> list[dict[str, Any]]:
        """Latest checks on ``sha``, excluding superseded Actions cancellations."""
        runs = await self.pages(
            f"commits/{sha}/check-runs", key="check_runs", params={"filter": "latest"}
        )
        if not any(run.get("conclusion") in {"cancelled", "stale"} for run in runs):
            return runs
        try:
            payloads = await self.pages(
                "actions/runs", key="workflow_runs", params={"head_sha": sha}
            )
            workflows = {
                workflow.check_suite_id: workflow
                for workflow in map(_WorkflowRun.model_validate, payloads)
            }
        except httpx2.HTTPError, ValueError:
            logger.warning(
                "Could not identify superseded check runs",
                extra={"repo_full_name": self.full_name, "sha": sha},
                exc_info=True,
            )
            return runs
        identities: dict[int, tuple[int, int, str, str]] = {}
        latest: dict[tuple[int, int, str, str], int] = {}
        orders: dict[int, int] = {}
        for index, run in enumerate(runs):
            app, suite = run.get("app"), run.get("check_suite")
            if not isinstance(app, dict) or not isinstance(suite, dict):
                continue
            app_id, suite_id = app.get("id"), suite.get("id")
            name = run.get("name")
            if (
                app.get("slug") != "github-actions"
                or not isinstance(app_id, int)
                or not isinstance(suite_id, int)
                or not isinstance(name, str)
                or (workflow := workflows.get(suite_id)) is None
            ):
                continue
            identity = (app_id, workflow.workflow_id, workflow.event, name)
            order = workflow.id
            identities[index], orders[index] = identity, order
            latest[identity] = max(latest.get(identity, order), order)
        return [
            run
            for index, run in enumerate(runs)
            if run.get("conclusion") not in {"cancelled", "stale"}
            or index not in identities
            or orders[index] == latest[identities[index]]
        ]

    async def workflow_for_suite(self, suite_id: int) -> WorkflowRun:
        runs = await self.pages(
            "actions/runs", key="workflow_runs", params={"check_suite_id": str(suite_id)}
        )
        parsed = [WorkflowRun.model_validate(run) for run in runs]
        if len(parsed) != 1 or parsed[0].check_suite_id != suite_id:
            raise ValueError("Required check workflow is ambiguous or unavailable")
        return parsed[0]

    async def merge_base(self, base: str, head: str) -> str:
        payload = await self.get(
            f"compare/{quote(base, safe='')}...{quote(head, safe='')}", {"per_page": "1"}
        )
        return CommitComparison.model_validate(payload).merge_base

    async def admission_artifact(self, run_id: int, name: str) -> dict[str, JsonValue]:
        artifacts = [
            Artifact.model_validate(item)
            for item in await self.pages(f"actions/runs/{run_id}/artifacts", key="artifacts")
        ]
        matches = [item for item in artifacts if item.name == name and not item.expired]
        if len(matches) != 1:
            raise ValueError("Admission evidence is missing or ambiguous")
        return await self.artifact_json(matches[0].id, "admission.json")

    async def artifact_json(self, artifact_id: int, filename: str) -> dict[str, JsonValue]:
        # GitHub redirects to a signed blob URL. Never forward authorization to it.
        response = await self.github.http.get(
            f"{GITHUB_API_BASE}/repos/{self.full_name}/actions/artifacts/{artifact_id}/zip",
            follow_redirects=False,
        )
        if response.status_code != 302:
            raise ValueError("Artifact download unavailable")
        location = response.headers.get("location", "")
        try:
            async with github_client() as anonymous:
                download = await request_with_safe_redirects(
                    anonymous, "GET", location, validate_url=_artifact_destination, stream=True
                )
                try:
                    download.raise_for_status()
                    content = bytearray()
                    async for chunk in download.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 1_000_000:
                            raise ValueError("Artifact is too large")
                finally:
                    await download.aclose()
        except httpx2.HTTPError:
            # Signed artifact URLs must not appear in controller exception logs.
            raise ValueError("Artifact download failed") from None
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            files = archive.infolist()
            if len(files) != 1 or files[0].filename != filename or files[0].file_size > 100_000:
                raise ValueError("Unexpected artifact contents")
            return TypeAdapter(dict[str, JsonValue]).validate_json(archive.read(files[0]))

    async def validation_runs(self, workflow: str, since: datetime) -> tuple[ValidationRun, ...]:
        runs: list[ValidationRun] = []
        for page in range(1, 11):
            result = ValidationRuns.model_validate(
                await self.get(
                    f"actions/workflows/{quote(workflow, safe='')}/runs",
                    {
                        "event": "workflow_dispatch",
                        "created": since.strftime(">=%Y-%m-%dT%H:%M:%SZ"),
                        "per_page": "100",
                        "page": str(page),
                    },
                )
            )
            runs.extend(result.workflow_runs)
            if len(runs) >= result.total_count:
                if len({run.id for run in runs}) != len(runs) or len(runs) != result.total_count:
                    raise ValueError("Workflow discovery changed during pagination")
                return tuple(runs)
            if not result.workflow_runs:
                break
        raise ValueError("Workflow discovery exceeds the bounded search window")

    async def validation_run(self, run_id: int) -> ValidationRun:
        return ValidationRun.model_validate(await self.get(f"actions/runs/{run_id}"))

    async def validation_jobs(self, run_id: int) -> tuple[ValidationJob, ...]:
        result = ValidationJobs.model_validate(
            await self.get(f"actions/runs/{run_id}/jobs", {"per_page": "100", "filter": "latest"})
        )
        if result.total_count != len(result.jobs):
            raise ValueError("Validation job listing is incomplete")
        return result.jobs

    async def validation_artifacts(self, run_id: int) -> tuple[Artifact, ...]:
        result = ValidationArtifacts.model_validate(
            await self.get(f"actions/runs/{run_id}/artifacts", {"per_page": "100"})
        )
        if result.total_count != len(result.artifacts):
            raise ValueError("Validation artifact listing is incomplete")
        return result.artifacts

    @asynccontextmanager
    async def candidate_git(self) -> AsyncIterator[CandidateGit]:
        """A dedicated sandbox with controller credentials kept in the GitHub proxy."""
        from openswe.github.repo_names import normalize_repo_full_name
        from openswe.merge_queue.git import CandidateGit
        from openswe.sandboxes.providers.langsmith import (
            configure_sandbox_proxy,
            create_langsmith_sandbox,
            get_async_sandbox_client,
        )

        repository = normalize_repo_full_name(self.full_name)
        if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Candidate Git requires a GitHub repository name")
        authorization = self.github.http.headers.get("Authorization")
        if (
            self.github.login is not None
            or not authorization
            or not authorization.startswith("Bearer ")
        ):
            raise ValueError("Candidate writes require an explicitly supplied controller identity")
        sandbox = await create_langsmith_sandbox(
            prepare_work_dir=False,
            create_params={"idle_ttl_seconds": 600, "delete_after_stop_seconds": 60},
        )
        try:
            await configure_sandbox_proxy(sandbox.id, authorization.removeprefix("Bearer "))
            git = CandidateGit(
                sandbox,
                PurePosixPath("/tmp/merge-queue.git"),
                f"https://github.com/{repository}.git",
            )
            await git.initialize()
            yield git
        finally:
            try:
                async with asyncio.timeout(30), get_async_sandbox_client() as client:
                    await client.stop_sandbox(sandbox.id)
            except Exception:
                logger.warning(
                    "Failed to stop candidate sandbox",
                    extra={"sandbox_id": sandbox.id},
                    exc_info=True,
                )

    async def commit_statuses(self, sha: str) -> list[dict[str, Any]]:
        """The latest status per context on ``sha``."""
        latest: dict[str, dict[str, Any]] = {}
        for status in await self.pages(f"commits/{sha}/status", key="statuses"):
            context = status.get("context")
            if isinstance(context, str):
                latest.setdefault(context, status)
        return list(latest.values())


def _artifact_destination(location: str) -> None:
    url = urlparse(location)
    if (
        url.scheme != "https"
        or not url.hostname
        or not url.hostname.endswith((".blob.core.windows.net", ".actions.githubusercontent.com"))
        or url.username
        or url.password
        or url.port not in (None, 443)
    ):
        raise ValueError("Untrusted artifact download destination")


async def or_none[T](read: Awaitable[T]) -> T | None:
    """``read``'s answer, or ``None`` when GitHub could not give one."""
    try:
        return await read
    except httpx2.HTTPError, ValueError:
        return None


def _json_or_none(response: httpx2.Response) -> object:
    return None if response.status_code == 204 or not response.content else response.json()


class _WorkflowRun(BaseModel):
    id: int
    workflow_id: int
    check_suite_id: int
    event: str


class _GitHubErrorBody(BaseModel):
    message: str = ""
    errors: list[object] = []


class GitHubError(httpx2.HTTPStatusError):
    """GitHub refused a request; ``message`` is GitHub's own explanation."""

    def __init__(self, response: httpx2.Response) -> None:
        super().__init__(
            f"GitHub answered {response.status_code}",
            request=response.request,
            response=response,
        )

    @property
    def message(self) -> str:
        fallback = f"GitHub request failed ({self.response.status_code})"
        try:
            body = _GitHubErrorBody.model_validate(self.response.json())
        except ValueError:
            return fallback
        details = "; ".join(
            str(error["message"]) if isinstance(error, dict) and "message" in error else str(error)
            for error in body.errors
        )
        if body.message and details:
            return f"{body.message}: {details}"
        return body.message or details or fallback


class GraphQLError(ValueError):
    """GitHub answered a GraphQL query with errors."""

    def __init__(self, errors: object) -> None:
        super().__init__(f"GitHub GraphQL errors: {errors}")
        self.errors = errors

    @property
    def message(self) -> str:
        messages = (
            str(error["message"])
            for error in (self.errors if isinstance(self.errors, list) else [])
            if isinstance(error, dict) and "message" in error
        )
        return "; ".join(messages) or "GitHub rejected the request"
