"""Supporting lookups for the PR media approval API."""

import logging

import httpx2
from fastapi import HTTPException

from agent.dashboard.profiles import get_valid_access_token
from agent.github import pr_media
from agent.github.app import get_github_app_installation_token
from agent.github.http import DEFAULT_TIMEOUT, github_headers

logger = logging.getLogger(__name__)


async def _bot_installation_token() -> str:
    token = await get_github_app_installation_token()
    if token is None:
        raise HTTPException(503, "GitHub App installation token is unavailable")
    return token


async def get_oauth_token_for_upload(login: str) -> str | None:
    """The approver's GitHub OAuth token, resolved and refreshed server-side."""
    return await get_valid_access_token(login)


async def resolve_repository(*, owner: str, repo: str) -> int:
    """Capture the immutable repository id and verify the PR is reachable.

    Uses the workspace GitHub App installation token — never a personal OAuth
    token — so preparing a request does not consume a human's credentials.
    Push access is deliberately not required: a reviewer may attach media to a
    PR they can read, and the upload itself re-uses the approver's token.
    """
    token = await _bot_installation_token()
    async with httpx2.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(
            f"{pr_media.GITHUB_API}/repos/{owner}/{repo}",
            headers=github_headers(token),
        )
    if response.status_code == 404:
        raise HTTPException(404, "repository not found or the GitHub App is not installed on it")
    if response.status_code != 200:
        raise HTTPException(502, f"GitHub repository lookup failed ({response.status_code})")
    data = response.json()
    repo_id = data.get("id")
    if not isinstance(repo_id, int):
        raise HTTPException(502, "GitHub repository lookup returned no repository id")
    return repo_id


async def fetch_pull_title(*, owner: str, repo: str, pull_number: int) -> str:
    token = await _bot_installation_token()
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
