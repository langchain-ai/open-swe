"""The repositories a user can pick from, served stale-while-revalidate."""

from typing import Any

from fastapi import APIRouter

from ...settings.repo_cache import (
    REPO_LIST_FRESH_MS,
    read_cached_repos,
    schedule_repo_cache_refresh,
    write_cached_repos,
)
from ..authz import SESSION
from ..repo_access import fetch_user_installations_and_repos

router = APIRouter()


async def _build_repo_payload(login: str) -> dict[str, Any]:
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
    session: dict[str, Any] = SESSION,
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
