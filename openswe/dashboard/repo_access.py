"""GitHub repository access checks for dashboard actions."""

import asyncio
import logging

import httpx2
from fastapi import HTTPException

from openswe.github.app import get_github_app_installation_token
from openswe.github.http import (
    GitHubAppUnavailable,
    GitHubClient,
    GitHubError,
    GitHubSignInRequired,
    github_reauth_message,
)
from openswe.github.repo_names import normalize_repo_full_name
from openswe.utils.http import DEFAULT_HTTP_TIMEOUT
from openswe.utils.http_pool import pooled_transport

logger = logging.getLogger(__name__)


class RepoAccessResponse:
    @staticmethod
    def require(response: httpx2.Response) -> None:
        status_code = response.status_code
        if status_code in (403, 429) and GitHubError(response).rate_limited:
            logger.warning(
                "GitHub repository access check rate limited",
                extra={
                    "github_status": status_code,
                    "github_request_id": response.headers.get("X-GitHub-Request-Id"),
                    "github_rate_limit_remaining": response.headers.get("X-RateLimit-Remaining"),
                    "github_rate_limit_reset": response.headers.get("X-RateLimit-Reset"),
                    "github_retry_after": response.headers.get("Retry-After"),
                },
            )
            retry_after = response.headers.get("Retry-After", "")
            headers = (
                {"Retry-After": retry_after}
                if retry_after.isascii() and retry_after.isdecimal()
                else None
            )
            raise HTTPException(
                429,
                "GitHub API rate limit reached. Try again later; relinking GitHub will not help.",
                headers=headers,
            )
        if status_code == 401:
            raise HTTPException(401, "GitHub credentials rejected")
        if status_code == 404:
            raise HTTPException(404, "repository not found")
        if status_code == 403:
            raise HTTPException(403, "no access to this private repository")
        if status_code != 200:
            raise HTTPException(502, f"github API error ({status_code})")

    @classmethod
    async def check(cls, github: GitHubClient, full_name: str) -> str:
        owner, name = normalize_repo_full_name(full_name).split("/", 1)
        try:
            await github.repo(owner, name).info(retry_rate_limits=False)
        except GitHubError as exc:
            cls.require(exc.response)
        except httpx2.TransportError as exc:
            logger.warning(
                "GitHub repository access check unavailable",
                extra={"repo_full_name": full_name, "error_type": type(exc).__name__},
            )
            raise HTTPException(
                503, "GitHub is temporarily unavailable. Try again shortly."
            ) from exc
        return github.http.headers["Authorization"].removeprefix("Bearer ")


async def assert_repo_access(full_name: str, token: str) -> str:
    full_name = normalize_repo_full_name(full_name)
    async with GitHubClient.connect(token=token, timeout=DEFAULT_HTTP_TIMEOUT) as github:
        await RepoAccessResponse.check(github, full_name)
    return full_name


class RepoAccessChecks:
    """GitHub repository read checks in flight, so a page's parallel reads share one.

    A check's answer is never kept past the check itself: access revoked on GitHub
    takes effect on the next request, and every waiter gets the token the check confirmed.
    """

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str, int], asyncio.Future[str]] = {}

    async def require(self, login: str, full_name: str) -> str:
        key = (
            login.lower(),
            normalize_repo_full_name(full_name).lower(),
            id(asyncio.get_running_loop()),
        )
        pending = self._pending.get(key)
        if pending is None:
            pending = asyncio.ensure_future(self._confirm(login, full_name))
            self._pending[key] = pending
            pending.add_done_callback(lambda _: self._pending.pop(key, None))
        return await asyncio.shield(pending)

    @staticmethod
    async def _confirm(login: str, full_name: str) -> str:
        try:
            async with GitHubClient.as_user(login, timeout=DEFAULT_HTTP_TIMEOUT) as github:
                return await RepoAccessResponse.check(github, full_name)
        except GitHubSignInRequired as exc:
            raise HTTPException(401, github_reauth_message(login)) from exc


_ACCESS_CHECKS = RepoAccessChecks()


async def require_repo_access_for_user(login: str, full_name: str) -> str:
    """The person's GitHub token, once GitHub confirms it can read ``full_name``."""
    return await _ACCESS_CHECKS.require(login, full_name)


async def require_repo_access_for_workspace(full_name: str) -> str:
    try:
        async with GitHubClient.as_app(timeout=DEFAULT_HTTP_TIMEOUT) as github:
            return await RepoAccessResponse.check(github, full_name)
    except GitHubAppUnavailable as exc:
        raise HTTPException(503, "workspace GitHub App token unavailable") from exc
    except HTTPException as exc:
        if exc.status_code == 401:
            raise HTTPException(502, "workspace GitHub App token rejected") from exc
        if exc.status_code in (403, 404):
            raise HTTPException(
                exc.status_code, "repository unavailable to the workspace GitHub App"
            ) from exc
        raise


async def repo_config_for_user(login: str, full_name: str | None) -> dict[str, str] | None:
    if not isinstance(full_name, str) or not full_name.strip():
        return None
    try:
        normalized = normalize_repo_full_name(full_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await require_repo_access_for_user(login, normalized)
    owner, name = normalized.split("/", 1)
    return {"owner": owner, "name": name}


async def repo_config_for_workspace(full_name: str | None) -> dict[str, str] | None:
    if not isinstance(full_name, str) or not full_name.strip():
        return None
    try:
        normalized = normalize_repo_full_name(full_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await require_repo_access_for_workspace(normalized)
    owner, name = normalized.split("/", 1)
    return {"owner": owner, "name": name}


async def repo_is_private(full_name: str) -> bool | None:
    """Whether ``full_name`` is private, read with the workspace GitHub App; ``None`` if unknown."""
    token = await get_github_app_installation_token()
    if not token:
        return None
    owner, name = normalize_repo_full_name(full_name).split("/", 1)
    try:
        async with httpx2.AsyncClient(
            transport=pooled_transport(), timeout=DEFAULT_HTTP_TIMEOUT
        ) as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{name}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx2.HTTPError:
        logger.warning(
            "Could not read repository visibility", extra={"repository": full_name}, exc_info=True
        )
        return None
    if response.status_code != 200:
        logger.warning(
            "Could not read repository visibility",
            extra={"repository": full_name, "github_status": response.status_code},
        )
        return None
    private = response.json().get("private")
    return private if isinstance(private, bool) else None
