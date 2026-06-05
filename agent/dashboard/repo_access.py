"""GitHub repository access checks for dashboard actions."""

from __future__ import annotations

import httpx
from fastapi import HTTPException

from .profiles import get_valid_access_token
from .review_styles import normalize_repo_full_name


def _raise_for_github_repo_status(status_code: int) -> None:
    if status_code == 401:
        raise HTTPException(401, "github token expired, re-login required")
    if status_code == 404:
        raise HTTPException(404, "repository not found")
    if status_code == 403:
        raise HTTPException(403, "no access to this private repository")
    if status_code != 200:
        raise HTTPException(502, f"github API error ({status_code})")


async def assert_repo_access(full_name: str, token: str) -> str:
    full_name = normalize_repo_full_name(full_name)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owner, name = full_name.split("/", 1)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{name}",
            headers=headers,
        )
        _raise_for_github_repo_status(response.status_code)
    return full_name


async def require_repo_access_for_user(login: str, full_name: str) -> str:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    try:
        await assert_repo_access(full_name, token)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        token = await get_valid_access_token(login, force_refresh=True)
        if not token:
            raise HTTPException(401, "github token expired, re-login required") from exc
        await assert_repo_access(full_name, token)
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
