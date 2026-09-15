"""Supporting lookups for the PR media approval API."""

import logging
from typing import Any

import httpx2
from fastapi import HTTPException

from agent.dashboard.profiles import get_valid_access_token
from agent.github import pr_media
from agent.github.http import DEFAULT_TIMEOUT, github_headers

logger = logging.getLogger(__name__)


async def get_oauth_token_for_upload(login: str) -> str | None:
    """The approver's GitHub OAuth token, resolved and refreshed server-side."""
    return await get_valid_access_token(login)


async def resolve_repository(login: str, *, owner: str, repo: str) -> dict[str, Any]:
    """Verify the preparer can push and capture the immutable repository id."""
    token = await get_valid_access_token(login)
    if token is None:
        raise HTTPException(401, "GitHub re-authentication required before preparing media")
    async with httpx2.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(
            f"{pr_media.GITHUB_API}/repos/{owner}/{repo}",
            headers=github_headers(token),
        )
    if response.status_code == 404:
        raise HTTPException(404, "repository not found or not accessible")
    if response.status_code != 200:
        raise HTTPException(502, f"GitHub repository lookup failed ({response.status_code})")
    data = response.json()
    repo_id = data.get("id")
    permissions = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    if permissions and permissions.get("push") is not True:
        raise HTTPException(403, "push access to the repository is required to attach media")
    if not isinstance(repo_id, int):
        raise HTTPException(502, "GitHub repository lookup returned no repository id")
    return {"repo_id": repo_id, "token": token}


async def fetch_pull_title(token: str, *, owner: str, repo: str, pull_number: int) -> str:
    async with httpx2.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(
            f"{pr_media.GITHUB_API}/repos/{owner}/{repo}/pulls/{pull_number}",
            headers=github_headers(token),
        )
    if response.status_code == 404:
        raise HTTPException(404, "pull request not found")
    if response.status_code != 200:
        raise HTTPException(502, f"GitHub pull request lookup failed ({response.status_code})")
    data = response.json()
    title = data.get("title")
    return title if isinstance(title, str) else ""
