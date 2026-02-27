"""GitHub webhook comment utilities."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Any

import httpx
from langgraph_sdk import get_client

from ..encryption import decrypt_token

logger = logging.getLogger(__name__)

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

OPEN_SWE_TAG = "@openswe"

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
        return True

    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def get_thread_id_from_branch(branch_name: str) -> str | None:
    """Extract the thread UUID from an Open SWE branch name.

    Open SWE branch names embed the thread UUID, e.g. open-swe/fix-bug-<uuid>.

    Args:
        branch_name: The git branch name from the PR.

    Returns:
        The thread UUID string, or None if not found.
    """
    match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        branch_name,
        re.IGNORECASE,
    )
    return match.group(0) if match else None


async def get_github_token_from_thread(thread_id: str) -> str | None:
    """Retrieve and decrypt the GitHub token from the most recent run of a thread.

    The github_token_encrypted is stored in the run's configurable, not in the
    thread state checkpoint config — so we fetch the latest run to access it.

    Args:
        thread_id: The LangGraph thread ID.

    Returns:
        The plaintext GitHub token, or None if unavailable.
    """
    langgraph_client = get_client(url=LANGGRAPH_URL)
    try:
        runs = await langgraph_client.runs.list(thread_id, limit=10)
        for run in runs:
            # Try top-level config first, then kwargs.config
            configurable = (
                (run.get("config") or {}).get("configurable")
                or (run.get("kwargs", {}) or {}).get("config", {}).get("configurable")
                or {}
            )
            encrypted = configurable.get("github_token_encrypted", "")
            if encrypted:
                token = decrypt_token(encrypted)
                if token:
                    logger.info("Found GitHub token in run config for thread %s", thread_id)
                    return token

        logger.warning("No github_token_encrypted found in any run for thread %s", thread_id)
        return None
    except Exception:
        logger.exception("Failed to get GitHub token from thread %s", thread_id)
        return None


async def react_to_github_comment(
    repo_config: dict[str, str], comment_id: int, *, event_type: str, token: str, pull_number: int | None = None, node_id: str | None = None
) -> bool:
    """Add a 👀 reaction to a GitHub PR comment.

    For pull_request_review events the REST API doesn't support reactions on
    the review body — uses GraphQL with node_id instead.

    Args:
        repo_config: Dict with 'owner' and 'name' keys.
        comment_id: The GitHub comment ID.
        event_type: One of 'issue_comment', 'pull_request_review_comment', 'pull_request_review'.
        token: GitHub access token.
        pull_number: PR number (unused, kept for future use).
        node_id: GraphQL node ID, required for 'pull_request_review' events.

    Returns:
        True if successful, False otherwise.
    """
    if event_type == "pull_request_review":
        return await _react_via_graphql(node_id, token=token)

    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")

    url_template = _REACTION_ENDPOINTS.get(event_type, _REACTION_ENDPOINTS["issue_comment"])
    url = url_template.format(owner=owner, repo=repo, comment_id=comment_id, pull_number=pull_number)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
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
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
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

    async with httpx.AsyncClient() as client:
        # 1. PR issue comments
        pr_comments = await _fetch_paginated(
            client,
            f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
            headers,
        )
        for c in pr_comments:
            all_comments.append({
                "body": c.get("body", ""),
                "author": c.get("user", {}).get("login", "unknown"),
                "created_at": c.get("created_at", ""),
                "type": "pr_comment",
                "comment_id": c.get("id"),
            })

        # 2. Inline review comments
        review_comments = await _fetch_paginated(
            client,
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            headers,
        )
        for c in review_comments:
            all_comments.append({
                "body": c.get("body", ""),
                "author": c.get("user", {}).get("login", "unknown"),
                "created_at": c.get("created_at", ""),
                "type": "review_comment",
                "comment_id": c.get("id"),
                "path": c.get("path", ""),
                "line": c.get("line") or c.get("original_line"),
            })

        # 3. Top-level review messages
        reviews = await _fetch_paginated(
            client,
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            headers,
        )
        for r in reviews:
            body = r.get("body", "")
            if not body:
                continue
            all_comments.append({
                "body": body,
                "author": r.get("user", {}).get("login", "unknown"),
                "created_at": r.get("submitted_at", ""),
                "type": "review",
                "comment_id": r.get("id"),
            })

    # Sort all comments chronologically
    all_comments.sort(key=lambda c: c.get("created_at", ""))

    # Find all @openswe mention positions
    tag_indices = [
        i
        for i, comment in enumerate(all_comments)
        if OPEN_SWE_TAG in (comment.get("body") or "").lower()
    ]

    if not tag_indices:
        return []

    # If this is the first @openswe invocation (only one tag), return ALL
    # comments so the agent has full context — inline review comments are
    # drafted before submission and appear earlier in the sorted list.
    # For repeat invocations, return everything since the previous tag.
    start = 0 if len(tag_indices) == 1 else tag_indices[-2] + 1
    return all_comments[start:]


async def fetch_pr_branch(repo_config: dict[str, str], pr_number: int) -> str:
    """Fetch the head branch name of a PR from the GitHub API.

    Used for issue_comment events where the branch is not in the webhook payload.

    Args:
        repo_config: Dict with 'owner' and 'name' keys.
        pr_number: The pull request number.

    Returns:
        The head branch name, or empty string if not found.
    """
    owner = repo_config.get("owner", "")
    repo = repo_config.get("name", "")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if response.status_code == 200:  # noqa: PLR2004
                return response.json().get("head", {}).get("ref", "")
    except Exception:
        logger.exception("Failed to fetch branch for PR %s", pr_number)
    return ""


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
