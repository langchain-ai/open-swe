"""Dashboard API for the repositories a user can reach through the GitHub App."""

from typing import Any

from fastapi import APIRouter, HTTPException

from agent.dashboard.deps import SESSION_DEP
from agent.dashboard.profiles import get_valid_access_token
from agent.github import repos
from agent.github.repo_cache import (
    REPO_LIST_FRESH_MS,
    read_cached_repos,
    schedule_repo_cache_refresh,
    write_cached_repos,
)
from agent.github.repo_merge_methods import RepositoryMergeMethods, repository_merge_methods

router = APIRouter(tags=["github"])


async def _build_repo_payload(login: str) -> dict[str, Any]:
    installations, repositories = await repos.fetch_user_installations_and_repos(login)
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
            {"full_name": r.get("full_name"), "private": r.get("private", False)}
            for r in repositories
            if r.get("full_name")
        ],
    }
    await write_cached_repos(login, payload)
    return payload


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
                schedule_repo_cache_refresh(login, lambda: _build_repo_payload(login))
            return payload
    return await _build_repo_payload(login)


@router.get("/repos/{owner}/{repo}/merge-methods")
async def api_repository_merge_methods(
    owner: str, repo: str, session: dict[str, Any] = SESSION_DEP
) -> RepositoryMergeMethods:
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub token unavailable, re-login required")
    return await repository_merge_methods(owner, repo, token)
