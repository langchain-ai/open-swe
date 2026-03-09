"""GitHub PR webhook utilities — fetching comments, reacting, and thread resolution."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

OPEN_SWE_BRANCH_PREFIX = "open-swe/"


def _gh_headers(token: str) -> dict[str, str]:
    return {**GITHUB_API_HEADERS, "Authorization": f"Bearer {token}"}


def verify_github_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        return True
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def extract_thread_id_from_branch(branch_name: str) -> str | None:
    """Extract the LangGraph thread ID from an open-swe branch name."""
    if not branch_name.startswith(OPEN_SWE_BRANCH_PREFIX):
        return None
    return branch_name[len(OPEN_SWE_BRANCH_PREFIX) :]


async def react_to_github_comment(
    owner: str,
    repo: str,
    comment_id: int,
    token: str,
    reaction: str = "eyes",
    *,
    is_pr_review_comment: bool = False,
) -> bool:
    """Add an emoji reaction to a GitHub comment."""
    if is_pr_review_comment:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/comments/{comment_id}/reactions"
    else:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers=_gh_headers(token),
                json={"content": reaction},
            )
            return response.status_code in (200, 201)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to react to GitHub comment %s", comment_id)
            return False


async def fetch_issue_comments(
    owner: str, repo: str, issue_number: int, token: str
) -> list[dict[str, Any]]:
    """Fetch all issue comments (general PR comments) for a PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
    comments: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            try:
                response = await client.get(
                    url,
                    headers=_gh_headers(token),
                    params={"per_page": "100", "page": str(page)},
                )
                if response.status_code != 200:  # noqa: PLR2004
                    break
                data = response.json()
                if not data:
                    break
                comments.extend(data)
                page += 1
            except httpx.HTTPError:
                logger.exception("Failed to fetch issue comments page %d", page)
                break
    return comments


async def fetch_pr_review_comments(
    owner: str, repo: str, pr_number: int, token: str
) -> list[dict[str, Any]]:
    """Fetch all review comments (inline code comments) for a PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    comments: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            try:
                response = await client.get(
                    url,
                    headers=_gh_headers(token),
                    params={"per_page": "100", "page": str(page)},
                )
                if response.status_code != 200:  # noqa: PLR2004
                    break
                data = response.json()
                if not data:
                    break
                comments.extend(data)
                page += 1
            except httpx.HTTPError:
                logger.exception("Failed to fetch PR review comments page %d", page)
                break
    return comments


async def fetch_pr_reviews(
    owner: str, repo: str, pr_number: int, token: str
) -> list[dict[str, Any]]:
    """Fetch all reviews (top-level review messages) for a PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    reviews: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            try:
                response = await client.get(
                    url,
                    headers=_gh_headers(token),
                    params={"per_page": "100", "page": str(page)},
                )
                if response.status_code != 200:  # noqa: PLR2004
                    break
                data = response.json()
                if not data:
                    break
                reviews.extend(data)
                page += 1
            except httpx.HTTPError:
                logger.exception("Failed to fetch PR reviews page %d", page)
                break
    return reviews


async def post_github_pr_comment(
    owner: str, repo: str, pr_number: int, token: str, body: str
) -> bool:
    """Post a comment on a GitHub PR (issue comment)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers=_gh_headers(token),
                json={"body": body},
            )
            return response.status_code == 201  # noqa: PLR2004
        except Exception:  # noqa: BLE001
            logger.exception("Failed to post comment on PR #%d", pr_number)
            return False


async def post_github_issue_comment(
    owner: str, repo: str, issue_number: int, token: str, body: str
) -> bool:
    """Post a comment on a GitHub issue."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers=_gh_headers(token),
                json={"body": body},
            )
            return response.status_code == 201  # noqa: PLR2004
        except Exception:  # noqa: BLE001
            logger.exception("Failed to post comment on issue #%d", issue_number)
            return False


def collect_comments_since_last_tag(
    comments: list[dict[str, Any]],
    triggering_comment_id: int | None = None,
) -> list[dict[str, Any]]:
    """Collect comments from the last @open-swe mention onward."""
    if not comments:
        return []

    sorted_comments = sorted(comments, key=lambda c: c.get("created_at", ""))

    trigger_idx = None
    if triggering_comment_id:
        for i, c in enumerate(sorted_comments):
            if c.get("id") == triggering_comment_id:
                trigger_idx = i
                break

    if trigger_idx is None:
        trigger_idx = len(sorted_comments) - 1

    prev_mention_idx = trigger_idx
    for i in range(trigger_idx - 1, -1, -1):
        body = sorted_comments[i].get("body", "")
        if re.search(r"@open-swe\b", body, re.IGNORECASE):
            prev_mention_idx = i
            break

    return sorted_comments[prev_mention_idx:]


def format_review_comment_for_prompt(comment: dict[str, Any]) -> str:
    """Format a PR review comment (inline code comment) for the agent prompt."""
    author = comment.get("user", {}).get("login", "Unknown")
    body = comment.get("body", "")
    path = comment.get("path", "")
    line = comment.get("line") or comment.get("original_line")
    start_line = comment.get("start_line") or comment.get("original_start_line")
    comment_id = comment.get("id", "")

    location = ""
    if path:
        location = f" in `{path}`"
        if start_line and line and start_line != line:
            location += f" (lines {start_line}-{line})"
        elif line:
            location += f" (line {line})"

    return f"**@{author}** (comment_id: {comment_id}){location}:\n{body}"
