"""GitHub reads and writes for the review guide: the PR's current head and the reader's approval."""

import logging

import httpx2
from fastapi import HTTPException
from pydantic import AliasPath, BaseModel, Field, ValidationError

from openswe.expedited_review.reviews import github_error, github_token_hint
from openswe.github.app import get_github_app_installation_token
from openswe.github.http import GITHUB_API_BASE, github_client, github_request
from openswe.github.pull_request_actions import MarkReadyAction
from openswe.web.profiles import get_valid_access_token

logger = logging.getLogger(__name__)


class _Ref(BaseModel):
    sha: str
    ref: str = ""


class PullRequestHead(BaseModel):
    title: str = ""
    state: str = "open"
    draft: bool = False
    author: str = Field("", validation_alias=AliasPath("user", "login"))
    head: _Ref
    base: _Ref


async def fetch_head(
    owner: str, repo: str, number: int, *, token: str | None = None
) -> PullRequestHead | None:
    """The PR as GitHub has it now, read with ``token`` or else the App's installation token."""
    token = token or await get_github_app_installation_token(repositories=[repo])
    if not token:
        logger.warning(
            "No installation token for the review guide",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
        )
        return None
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
        return PullRequestHead.model_validate_json(response.content)
    except httpx2.HTTPError, ValidationError:
        logger.exception(
            "Could not fetch the pull request for the review guide",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
        )
        return None


async def approve(
    *, login: str, owner: str, repo: str, number: int, head_sha: str, body: str
) -> str | None:
    """Submit ``login``'s ``APPROVE`` review on ``head_sha``; why it failed, or ``None``."""
    token = await get_valid_access_token(login)
    if not token:
        return f"Open SWE has no GitHub token for @{login}. {github_token_hint()}"
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/reviews"
    payload = {"commit_id": head_sha, "event": "APPROVE", "body": body}
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "POST", url, json=payload)
            response.raise_for_status()
    except httpx2.HTTPStatusError as exc:
        return f"GitHub rejected @{login}'s review: {github_error(exc.response)}"
    except httpx2.HTTPError:
        logger.warning("GitHub did not answer a review guide approval", exc_info=True)
        return f"GitHub did not answer when submitting @{login}'s review."
    return None


async def mark_ready(*, login: str, owner: str, repo: str, number: int) -> str | None:
    """Take the PR out of draft as ``login``; why it failed, or ``None``."""
    token = await get_valid_access_token(login)
    if not token:
        return f"Open SWE has no GitHub token for @{login}. {github_token_hint()}"
    try:
        async with github_client(token=token) as client:
            await MarkReadyAction(action="mark-ready").perform(client, owner, repo, number)
    except HTTPException as exc:
        logger.warning(
            "GitHub refused to mark a guided pull request ready",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
        )
        return f"GitHub did not mark the pull request ready: {exc.detail}"
    return None
