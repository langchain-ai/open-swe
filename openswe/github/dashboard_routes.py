"""Dashboard API for the repositories a user can reach through the GitHub App."""

from typing import Any

from fastapi import APIRouter

from openswe.dashboard.deps import SESSION_DEP
from openswe.github.http import GitHubClient
from openswe.github.repo_cache import (
    REPO_LIST_FRESH_MS,
    read_cached_repos,
    schedule_repo_cache_refresh,
)
from openswe.github.repo_merge_methods import RepositoryMergeMethods, repository_merge_methods
from openswe.github.repos import build_repo_payload

router = APIRouter(tags=["github"])


@router.get("/repos")
async def list_repos(
    refresh: bool = False,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    """List repos where Open SWE is installed and the user has access.

    Served from the per-login cache (stale-while-revalidate) unless
    ``refresh=true``, because the fan-out over every installation takes 10s+
    for users with hundreds of accessible repos.
    """
    login = session["sub"]
    if not refresh:
        cached = await read_cached_repos(login)
        if cached is not None:
            payload, age_ms = cached
            if age_ms > REPO_LIST_FRESH_MS:
                schedule_repo_cache_refresh(login, lambda: build_repo_payload(login))
            return payload
    return await build_repo_payload(login)


@router.get("/repos/{owner}/{repo}/merge-methods")
async def api_repository_merge_methods(
    owner: str, repo: str, session: dict[str, Any] = SESSION_DEP
) -> RepositoryMergeMethods:
    async with GitHubClient.as_user(session["sub"]) as github:
        return await repository_merge_methods(github.repo(owner, repo))
