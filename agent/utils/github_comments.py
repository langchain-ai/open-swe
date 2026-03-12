"""GitHub webhook comment utilities."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
from typing import Any

import httpx

from .github_user_email_map import GITHUB_USER_EMAIL_MAP

logger = logging.getLogger(__name__)

OPEN_SWE_TAGS = ("@openswe", "@open-swe", "@openswe-dev")
UNTRUSTED_GITHUB_COMMENT_OPEN_TAG = "<dangerous-external-untrusted-users-comment>"
UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG = "</dangerous-external-untrusted-users-comment>"
_SANITIZED_UNTRUSTED_GITHUB_COMMENT_OPEN_TAG = "[blocked-untrusted-comment-tag-open]"
_SANITIZED_UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG = "[blocked-untrusted-comment-tag-close]"

# Reaction endpoint differs per comment type
_REACTION_ENDPOINTS: dict[str, str] = {
    "issue_comment": "https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions",
    "pull_request_review_comment": "https://api.github.com/repos/{owner}/{repo}/pulls/comments/{comment_id}/reactions",
    "pull_request_review": "https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{comment_id}/reactions",
}


def verify_github_signature(body: bytes, signature: str, *, secret: str) -> bool:
    """Verify the GitHub webhook signature (X-Hub-Signature-256).

    Args:
        body: Raw request body bytes.
        signature: The X-Hub-Signature-256 header value.
        secret: The webhook signing secret.

    Returns:
        True if signature is valid or no secret is configured.
    """
    if not secret:
        logger.warning("GITHUB_WEBHOOK_SECRET is not configured — rejecting webhook request")
        return False

    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def get_thread_id_from_branch(branch_name: str) -> str | None:
    match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        branch_name,
        re.IGNORECASE,
    )
    return match.group(0) if match else None


def sanitize_github_comment_body(body: str) -> str:
    """Strip reserved trust wrapper tags from raw GitHub comment bodies."""
    sanitized = body.replace(
        UNTRUSTED_GITHUB_COMMENT_OPEN_TAG,
        _SANITIZED_UNTRUSTED_GITHUB_COMMENT_OPEN_TAG,
    ).replace(
        UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG,
        _SANITIZED_UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG,
    )
    if sanitized != body:
        logger.warning("Sanitized reserved untrusted-comment tags from GitHub comment body")
    return sanitized


def format_github_comment_body_for_prompt(author: str, body: str) -> str:
    """Format a GitHub comment body for prompt inclusion."""
    sanitized_body = sanitize_github_comment_body(body)
    if author in GITHUB_USER_EMAIL_MAP:
        return sanitized_body

    return (
        f"{UNTRUSTED_GITHUB_COMMENT_OPEN_TAG}\n"
        f"{sanitized_body}\n"
        f"{UNTRUSTED_GITHUB_COMMENT_CLOSE_TAG}"
    )


async def react_to_github_comment(
    repo_config: dict[str, str],
    comment_id: int,
    *,
    event_type: str,
    token: str,
    pull_number: int | None = None,
    node_id: str | None = None,
) -> bool:
    if event_type == "pull_request_review":
        return await _react_via_graphql(node_id, token=token)

    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")

    url_template = _REACTION_ENDPOINTS.get(event_type, _REACTION_ENDPOINTS["issue_comment"])
    url = url_template.format(
        owner=owner, repo=repo, comment_id=comment_id, pull_number=pull_number
    )

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"content": "eyes"},
            )
            # 200 = already reacted, 201 = just created
            return response.status_code in (200, 201)
        except Exception:
            logger.exception("Failed to react to GitHub comment %s", comment_id)
            return False


async def _react_via_graphql(node_id: str | None, *, token: str) -> bool:
    """Add a 👀 reaction via GitHub GraphQL API (for PR review bodies)."""
    if not node_id:
        logger.warning("No node_id provided for GraphQL reaction")
        return False

    query = """
    mutation AddReaction($subjectId: ID!) {
    addReaction(input: {subjectId: $subjectId, content: EYES}) {
        reaction { content }
    }
    }
    """
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                "https://api.github.com/graphql",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query, "variables": {"subjectId": node_id}},
            )
            data = response.json()
            if "errors" in data:
                logger.warning("GraphQL reaction errors: %s", data["errors"])
                return False
            return True
        except Exception:
            logger.exception("Failed to react via GraphQL for node_id %s", node_id)
            return False


async def post_github_comment(
    repo_config: dict[str, str],
    issue_number: int,
    body: str,
    *,
    token: str,
) -> bool:
    """Post a comment to a GitHub issue or PR."""
    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json={"body": body},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            logger.exception("Failed to post comment to GitHub issue/PR #%s", issue_number)
            return False


async def fetch_issue_comments(
    repo_config: dict[str, str], issue_number: int, *, token: str | None = None
) -> list[dict[str, Any]]:
    """Fetch all comments for a GitHub issue."""
    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as http_client:
        comments = await _fetch_paginated(
            http_client,
            f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments",
            headers,
        )

    return [
        {
            "body": comment.get("body", ""),
            "author": comment.get("user", {}).get("login", "unknown"),
            "created_at": comment.get("created_at", ""),
            "comment_id": comment.get("id"),
        }
        for comment in comments
    ]


async def fetch_pr_comments_since_last_tag(
    repo_config: dict[str, str], pr_number: int, *, token: str
) -> list[dict[str, Any]]:
    """Fetch all PR comments/reviews since the last @open-swe tag.

    Fetches from all 3 GitHub comment sources, merges and sorts chronologically,
    then returns every comment from the last @open-swe mention onwards.

    For inline review comments the dict also includes:
    - 'path': file path commented on
    - 'line': line number
    - 'comment_id': GitHub comment ID (for future reply tooling)

    Args:
        repo_config: Dict with 'owner' and 'name' keys.
        pr_number: The pull request number.
        token: GitHub access token.

    Returns:
        List of comment dicts ordered chronologically from last @open-swe tag.
    """
    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    all_comments: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as http_client:
        pr_comments, review_comments, reviews = await asyncio.gather(
            _fetch_paginated(
                http_client,
                f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
                headers,
            ),
            _fetch_paginated(
                http_client,
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                headers,
            ),
            _fetch_paginated(
                http_client,
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers,
            ),
        )

    for c in pr_comments:
        all_comments.append(
            {
                "body": c.get("body", ""),
                "author": c.get("user", {}).get("login", "unknown"),
                "created_at": c.get("created_at", ""),
                "type": "pr_comment",
                "comment_id": c.get("id"),
            }
        )
    for c in review_comments:
        all_comments.append(
            {
                "body": c.get("body", ""),
                "author": c.get("user", {}).get("login", "unknown"),
                "created_at": c.get("created_at", ""),
                "type": "review_comment",
                "comment_id": c.get("id"),
                "path": c.get("path", ""),
                "line": c.get("line") or c.get("original_line"),
            }
        )
    for r in reviews:
        body = r.get("body", "")
        if not body:
            continue
        all_comments.append(
            {
                "body": body,
                "author": r.get("user", {}).get("login", "unknown"),
                "created_at": r.get("submitted_at", ""),
                "type": "review",
                "comment_id": r.get("id"),
            }
        )

    # Sort all comments chronologically
    all_comments.sort(key=lambda c: c.get("created_at", ""))

    # Find all @openswe / @open-swe mention positions
    tag_indices = [
        i
        for i, comment in enumerate(all_comments)
        if any(tag in (comment.get("body") or "").lower() for tag in OPEN_SWE_TAGS)
    ]

    if not tag_indices:
        return []

    # If this is the first @openswe invocation (only one tag), return ALL
    # comments so the agent has full context — inline review comments are
    # drafted before submission and appear earlier in the sorted list.
    # For repeat invocations, return everything since the previous tag.
    start = 0 if len(tag_indices) == 1 else tag_indices[-2] + 1
    return all_comments[start:]


async def fetch_pr_branch(
    repo_config: dict[str, str], pr_number: int, *, token: str | None = None
) -> str:
    """Fetch the head branch name of a PR from the GitHub API.

    Used for issue_comment events where the branch is not in the webhook payload.
    Token is optional — omitting it makes an unauthenticated request (lower rate limit).

    Args:
        repo_config: Dict with 'owner' and 'name' keys.
        pr_number: The pull request number.
        token: GitHub access token (optional).

    Returns:
        The head branch name, or empty string if not found.
    """
    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=headers,
            )
            if response.status_code == 200:  # noqa: PLR2004
                return response.json().get("head", {}).get("ref", "")
    except Exception:
        logger.exception("Failed to fetch branch for PR %s", pr_number)
    return ""


async def extract_pr_context(
    payload: dict[str, Any], event_type: str
) -> tuple[dict[str, str], int | None, str, str, str, int | None, str | None]:
    """Extract key fields from a GitHub PR webhook payload.

    Returns:
        (repo_config, pr_number, branch_name, github_login, pr_url, comment_id, node_id)
    """
    repo = payload.get("repository", {})
    repo_config = {"owner": repo.get("owner", {}).get("login", ""), "name": repo.get("name", "")}

    pr_data = payload.get("pull_request") or payload.get("issue", {})
    pr_number = pr_data.get("number")
    pr_url = pr_data.get("html_url", "") or pr_data.get("url", "")
    branch_name = (payload.get("pull_request") or {}).get("head", {}).get("ref", "")

    if not branch_name and pr_number:
        branch_name = await fetch_pr_branch(repo_config, pr_number)

    github_login = payload.get("sender", {}).get("login", "")

    comment = payload.get("comment") or payload.get("review", {})
    comment_id = comment.get("id")
    node_id = comment.get("node_id") if event_type == "pull_request_review" else None

    return repo_config, pr_number, branch_name, github_login, pr_url, comment_id, node_id


def build_pr_prompt(comments: list[dict[str, Any]], pr_url: str) -> str:
    """Format PR comments into a human message for the agent."""
    lines: list[str] = []
    for c in comments:
        author = c.get("author", "unknown")
        body = format_github_comment_body_for_prompt(author, c.get("body", ""))
        if c.get("type") == "review_comment":
            path = c.get("path", "")
            line = c.get("line", "")
            loc = f" (file: `{path}`, line: {line})" if path else ""
            lines.append(f"\n**{author}**{loc}:\n{body}\n")
        else:
            lines.append(f"\n**{author}**:\n{body}\n")

    comments_text = "".join(lines)
    return (
        "You've been tagged in GitHub PR comments. Please resolve them.\n\n"
        f"PR: {pr_url}\n\n"
        f"## Comments:\n{comments_text}\n\n"
        "If code changes are needed:\n"
        "1. Make the changes in the sandbox\n"
        "2. Call `commit_and_open_pr` to push them to GitHub — this is REQUIRED, do NOT skip it\n"
        "3. Call `github_comment` with the PR number to post a summary on GitHub\n\n"
        "If no code changes are needed:\n"
        "1. Call `github_comment` with the PR number to explain your answer — this is REQUIRED, never end silently\n\n"
        "**You MUST always call `github_comment` before finishing — whether or not changes were made.**"
    )


async def _fetch_paginated(
    client: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> list[dict[str, Any]]:
    """Fetch all pages from a GitHub paginated endpoint.

    Args:
        client: An active httpx async client.
        url: The GitHub API endpoint URL.
        headers: Auth + accept headers.

    Returns:
        Combined list of all items across pages.
    """
    results: list[dict[str, Any]] = []
    params: dict[str, Any] = {"per_page": 100, "page": 1}

    while True:
        try:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code != 200:  # noqa: PLR2004
                logger.warning("GitHub API returned %s for %s", response.status_code, url)
                break
            page_data = response.json()
            if not page_data:
                break
            results.extend(page_data)
            if len(page_data) < 100:  # noqa: PLR2004
                break
            params["page"] += 1
        except Exception:
            logger.exception("Failed to fetch %s", url)
            break

    return results
