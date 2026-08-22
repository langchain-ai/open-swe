"""What GitHub repositories a dashboard user may act on, and how we find out.

Also the single place the dashboard turns a GitHub status code into an
``HTTPException`` and the single place it retries a user-token call after a
401 refresh — both used to exist twice, with different wording.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from fastapi import HTTPException

from ..github.api import (
    DEFAULT_MAX_RETRIES,
    github_client,
    github_paginate,
    github_request,
    github_url,
)
from ..settings.github_tokens import get_valid_access_token
from ..settings.review_styles import normalize_repo_full_name

logger = logging.getLogger(__name__)

T = TypeVar("T")

_GITHUB_API_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
# An installation a user can see but whose repository list they may not read:
# skip it instead of failing the whole listing.
_SKIPPABLE_INSTALLATION_REPO_STATUS_CODES = frozenset({403, 404})


def github_http_exception(status_code: int) -> HTTPException:
    """Map a GitHub response status onto the error the dashboard returns."""
    if status_code == 401:
        return HTTPException(401, "github token expired, re-login required")
    if status_code == 403:
        return HTTPException(403, "no access to this private repository")
    if status_code == 404:
        return HTTPException(404, "repository not found")
    return HTTPException(502, f"github API error ({status_code})")


def raise_for_github_status(status_code: int) -> None:
    """Raise the mapped ``HTTPException`` unless the status is a 200."""
    if status_code != 200:
        raise github_http_exception(status_code)


async def with_user_github_token(login: str, call: Callable[[str], Awaitable[T]]) -> tuple[str, T]:
    """Run ``call`` with ``login``'s GitHub token, refreshing once on a 401.

    Returns ``(token, result)`` — the token that actually worked, since callers
    reuse it for follow-up requests. A user access token can expire mid-session;
    forcing a refresh and retrying is the difference between a working dashboard
    and an unexplained re-login prompt.
    """
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    try:
        return token, await call(token)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        refreshed = await get_valid_access_token(login, force_refresh=True)
        if not refreshed:
            raise HTTPException(401, "github token expired, re-login required") from exc
        return refreshed, await call(refreshed)


async def assert_repo_access(full_name: str, token: str) -> str:
    full_name = normalize_repo_full_name(full_name)
    owner, name = full_name.split("/", 1)
    async with github_client(token=token) as client:
        response = await github_request(client, "GET", github_url(f"/repos/{owner}/{name}"))
    raise_for_github_status(response.status_code)
    return full_name


async def require_repo_access_for_user(login: str, full_name: str) -> str:
    token, _ = await with_user_github_token(login, lambda t: assert_repo_access(full_name, t))
    return token


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


async def filter_repo_records_for_user(
    login: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop the per-repo records whose repository ``login`` cannot reach."""
    out: list[dict[str, Any]] = []
    for record in records:
        full_name = record.get("full_name")
        if not isinstance(full_name, str):
            continue
        try:
            await require_repo_access_for_user(login, full_name)
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                continue
            raise
        out.append(record)
    return out


async def paginate_github(
    client: httpx.AsyncClient,
    url: str,
    *,
    items_key: str | None,
    cap: int = 1000,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> list[dict[str, Any]]:
    """:func:`github_paginate` with GitHub failures mapped to dashboard errors."""
    try:
        return await github_paginate(
            client, url, items_key=items_key, cap=cap, max_retries=max_retries
        )
    except httpx.TimeoutException as exc:
        logger.warning("GitHub API timed out while paginating %s", url)
        raise HTTPException(503, "github API request timed out") from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("GitHub API returned %s while paginating %s", exc.response.status_code, url)
        raise github_http_exception(exc.response.status_code) from exc
    except httpx.RequestError as exc:
        logger.warning("GitHub API request failed while paginating %s: %s", url, exc)
        raise HTTPException(502, "github API request failed") from exc


async def fetch_user_installations_and_repos(
    login: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve the installations and repos a user can access via the GitHub App.

    Paginates both ``/user/installations`` and per-installation
    ``/user/installations/{id}/repositories`` so users with multiple
    installations or >30 accessible repos get the complete set. Shared by the
    ``/repos`` endpoint and the reviews access filter.
    """

    async def list_installations(token: str) -> list[dict[str, Any]]:
        async with github_client(token=token, timeout=_GITHUB_API_TIMEOUT) as client:
            return await paginate_github(
                client, github_url("/user/installations"), items_key="installations"
            )

    token, installations = await with_user_github_token(login, list_installations)

    repositories: list[dict[str, Any]] = []
    async with github_client(token=token, timeout=_GITHUB_API_TIMEOUT) as client:
        for inst in installations:
            inst_id = inst.get("id")
            if inst_id is None:
                continue
            try:
                repos = await paginate_github(
                    client,
                    github_url(f"/user/installations/{inst_id}/repositories"),
                    items_key="repositories",
                )
            except HTTPException as exc:
                if exc.status_code in _SKIPPABLE_INSTALLATION_REPO_STATUS_CODES:
                    logger.warning(
                        "Skipping installation %s repository list: %s", inst_id, exc.detail
                    )
                    continue
                raise
            repositories.extend(repos)
    return installations, repositories


async def accessible_repo_full_names(login: str) -> frozenset[str]:
    """Lowercased ``owner/name`` of repos the user can currently access.

    Resolved fresh on every call (a fixed, repo-count-independent burst of
    GitHub calls) rather than cached. ``/reviews`` uses this set to decide
    which private PR metadata a user may see, so it's an authorization
    boundary: a stale set would leak repo/PR titles, branches, authors and
    finding counts for repos the user just lost access to.
    """
    _, repositories = await fetch_user_installations_and_repos(login)
    return frozenset(
        repo["full_name"].lower() for repo in repositories if isinstance(repo.get("full_name"), str)
    )
