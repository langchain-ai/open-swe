"""Dashboard API for the repos a user can reach through the GitHub App."""

import logging
from collections.abc import AsyncIterable
from typing import Any, TypedDict

from fastapi import HTTPException
from githubkit.auth import TokenAuthStrategy
from githubkit.exception import RequestError, RequestFailed, RequestTimeout

from openswe.dashboard.profiles import get_valid_access_token
from openswe.github import repo_cache
from openswe.github.sdk import GITHUB_API_VERSION, github_sdk

logger = logging.getLogger(__name__)

_SKIPPABLE_INSTALLATION_REPO_STATUS_CODES = frozenset({403, 404})
# How long ``accessible_repo_full_names`` trusts a cached listing. It is an
# authorization boundary (it decides which private PR metadata a person may
# see): installation webhooks clear the cache, so a repo removed from the App
# drops at once, while access lost on GitHub's side, such as leaving an org,
# shows within this window.
ACCESS_FRESH_MS = 60 * 1000


class InstallationAccount(TypedDict):
    login: str | None
    type: str | None


class InstallationSummary(TypedDict):
    id: int
    account: InstallationAccount | None


class RepositorySummary(TypedDict):
    full_name: str
    private: bool
    archived: bool


def _github_api_http_exception(status_code: int) -> HTTPException:
    if status_code == 401:
        return HTTPException(401, "github token expired, re-login required")
    if status_code == 403:
        return HTTPException(403, "github API forbidden")
    if status_code == 404:
        return HTTPException(404, "github API resource not found")
    return HTTPException(502, f"github API error ({status_code})")


async def _collect[T](items: AsyncIterable[T], *, cap: int = 1000) -> list[T]:
    out: list[T] = []
    try:
        async for item in items:
            out.append(item)
            if len(out) >= cap:
                break
    except RequestTimeout as exc:
        logger.warning("GitHub API timed out while paginating")
        raise HTTPException(503, "github API request timed out") from exc
    except RequestFailed as exc:
        logger.warning(
            "GitHub API returned an error while paginating",
            extra={"status_code": exc.response.status_code},
        )
        raise _github_api_http_exception(exc.response.status_code) from exc
    except RequestError as exc:
        logger.warning("GitHub API request failed while paginating", exc_info=True)
        raise HTTPException(502, "github API request failed") from exc
    return out


async def fetch_user_installations_and_repos(
    login: str,
) -> tuple[list[InstallationSummary], list[RepositorySummary]]:
    """Resolve the installations and repos a user can access via the GitHub App."""
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    try:
        return await _fetch_with_token(token)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        token = await get_valid_access_token(login, force_refresh=True)
        if not token:
            raise HTTPException(401, "github token expired, re-login required") from exc
        return await _fetch_with_token(token)


async def _fetch_with_token(
    token: str,
) -> tuple[list[InstallationSummary], list[RepositorySummary]]:
    headers = {"X-GitHub-Api-Version": GITHUB_API_VERSION}
    async with github_sdk(TokenAuthStrategy(token), timeout=10.0, connect_timeout=3.0) as client:
        apps = client.rest(GITHUB_API_VERSION).apps
        records = await _collect(
            client.rest.paginate(
                apps.async_list_installations_for_authenticated_user,
                map_func=lambda response: response.json()["installations"],
                per_page=100,
                headers=headers,
            )
        )
        installations: list[InstallationSummary] = []
        repositories: list[RepositorySummary] = []
        for record in records:
            account = record.get("account")
            installations.append(
                {
                    "id": record["id"],
                    "account": {"login": account.get("login"), "type": account.get("type")}
                    if account
                    else None,
                }
            )
            try:
                repos = await _collect(
                    client.rest.paginate(
                        apps.async_list_installation_repos_for_authenticated_user,
                        installation_id=record["id"],
                        map_func=lambda response: response.json()["repositories"],
                        per_page=100,
                        headers=headers,
                    )
                )
            except HTTPException as exc:
                if exc.status_code in _SKIPPABLE_INSTALLATION_REPO_STATUS_CODES:
                    logger.warning(
                        "Skipping inaccessible installation repository list",
                        extra={"installation_id": record["id"], "status_code": exc.status_code},
                    )
                    continue
                raise
            repositories.extend(
                {
                    "full_name": repo["full_name"],
                    "private": repo["private"],
                    "archived": repo.get("archived", False),
                }
                for repo in repos
            )
    return installations, repositories


async def build_repo_payload(login: str) -> dict[str, Any]:
    """List the person's installations and repos from GitHub now, and cache the listing."""
    installations, repositories = await fetch_user_installations_and_repos(login)
    payload = {
        "installations": [
            {
                "id": i.get("id"),
                "account": (i.get("account") or {}).get("login"),
                "account_type": (i.get("account") or {}).get("type"),
            }
            for i in installations
        ],
        "repositories": [
            {
                "full_name": r.get("full_name"),
                "private": r.get("private", False),
                "archived": r.get("archived", False),
            }
            for r in repositories
            if r.get("full_name")
        ],
    }
    await repo_cache.write_cached_repos(login, payload)
    return payload


async def accessible_repo_full_names(login: str) -> frozenset[str]:
    """Lowercased ``owner/name`` of repos the user can access, as of ``ACCESS_FRESH_MS`` ago at most."""
    cached = await repo_cache.read_cached_repos(login)
    if cached is not None and cached[1] <= ACCESS_FRESH_MS:
        payload = cached[0]
    else:
        payload = await build_repo_payload(login)
    repositories = payload.get("repositories")
    return frozenset(
        repo["full_name"].lower()
        for repo in (repositories if isinstance(repositories, list) else [])
        if isinstance(repo, dict) and isinstance(repo.get("full_name"), str)
    )
