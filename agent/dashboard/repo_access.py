"""GitHub repository access checks for dashboard actions."""

import logging
from datetime import timedelta

import httpx2
from fastapi import HTTPException

from agent.dashboard.profiles import get_valid_access_token
from agent.github.app import get_github_app_installation_token
from agent.review.styles import normalize_repo_full_name
from agent.utils.http import DEFAULT_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

# Bounds how long a user who lost GitHub access can keep reading App-token data.
REPO_ACCESS_FRESH_FOR = timedelta(seconds=30)
REPO_ACCESS_MAX_AGE = timedelta(seconds=60)


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
    async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{name}",
            headers=headers,
        )
        _raise_for_github_repo_status(response.status_code)
    return full_name


async def require_repo_access_for_user(login: str, full_name: str) -> str:
    from langgraph_api.cache import swr

    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    verified_token = token

    async def verify() -> bool:
        nonlocal verified_token
        verified_token = await _verify_repo_access_for_user(login, full_name, token)
        return True

    key = f"repo-access:{login}:{normalize_repo_full_name(full_name)}".lower()
    await swr(key, verify, fresh_for=REPO_ACCESS_FRESH_FOR, max_age=REPO_ACCESS_MAX_AGE)
    return verified_token


async def _verify_repo_access_for_user(login: str, full_name: str, token: str) -> str:
    try:
        await assert_repo_access(full_name, token)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        refreshed = await get_valid_access_token(login, force_refresh=True)
        if not refreshed:
            raise HTTPException(401, "github token expired, re-login required") from exc
        await assert_repo_access(full_name, refreshed)
        return refreshed
    return token


async def require_repo_access_for_workspace(full_name: str) -> str:
    token = await get_github_app_installation_token()
    if not token:
        raise HTTPException(503, "workspace GitHub App token unavailable")
    try:
        await assert_repo_access(full_name, token)
    except HTTPException as exc:
        if exc.status_code == 401:
            raise HTTPException(502, "workspace GitHub App token rejected") from exc
        if exc.status_code in (403, 404):
            raise HTTPException(
                exc.status_code, "repository unavailable to the workspace GitHub App"
            ) from exc
        raise
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
        async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
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
