"""Dashboard API for the repositories a user can reach through the GitHub App, and allowed bots."""

from typing import Any

from fastapi import APIRouter, HTTPException

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP
from openswe.dashboard.oauth import session_user_id
from openswe.github.allowed_bots import AllowedGitHubBot, AllowGitHubBot
from openswe.github.http import GitHubClient
from openswe.github.org_membership import OPEN_SWE_GITHUB_LOGINS
from openswe.github.repo_cache import (
    REPO_LIST_FRESH_MS,
    read_cached_repos,
    schedule_repo_cache_refresh,
)
from openswe.github.repo_merge_methods import RepositoryMergeMethods, repository_merge_methods
from openswe.github.repos import build_repo_payload
from openswe.users.records import user_id_for_login

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


@router.get("/github/allowed-bots")
async def api_list_allowed_github_bots(
    _admin: dict[str, Any] = ADMIN_DEP,
) -> list[AllowedGitHubBot]:
    return await AllowedGitHubBot.all()


@router.post("/github/allowed-bots")
@audit_endpoint
async def api_allow_github_bot(
    body: AllowGitHubBot,
    admin: dict[str, Any] = ADMIN_DEP,
) -> AllowedGitHubBot:
    if body.login in OPEN_SWE_GITHUB_LOGINS:
        raise HTTPException(400, "Open SWE's own bots are always allowed.")
    actor = session_user_id(admin) or user_id_for_login(admin["sub"])
    if not await AllowedGitHubBot.add(body.login, actor):
        raise HTTPException(409, "This bot is already allowed.")
    bot = await AllowedGitHubBot.get(body.login)
    if bot is None:
        raise HTTPException(409, "This bot was removed meanwhile.")
    return bot


@router.delete("/github/allowed-bots/{login}")
@audit_endpoint
async def api_remove_allowed_github_bot(
    login: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> dict[str, bool]:
    await AllowedGitHubBot.remove(login)
    return {"ok": True}
