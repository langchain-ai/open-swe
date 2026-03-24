import asyncio
from typing import Any

import httpx
from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token

GITHUB_API_BASE = "https://api.github.com"


def _get_repo_config() -> dict[str, str]:
    config = get_config()
    return config.get("configurable", {}).get("repo", {})


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get_token() -> str | None:
    return await get_github_app_installation_token()


def _repo_url(repo_config: dict[str, str]) -> str:
    owner = repo_config.get("owner", "")
    name = repo_config.get("name", "")
    return f"{GITHUB_API_BASE}/repos/{owner}/{name}"


def list_pr_reviews(pull_number: int) -> dict[str, Any]:
    """List all reviews on a pull request."""
    repo_config = _get_repo_config()
    if not repo_config:
        return {"success": False, "error": "No repo config found"}

    token = asyncio.run(_get_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    url = f"{_repo_url(repo_config)}/pulls/{pull_number}/reviews"

    async def _fetch() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=_github_headers(token))
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"GitHub API returned {response.status_code}: {response.text}",
                }
            return {"success": True, "reviews": response.json()}

    return asyncio.run(_fetch())


def get_pr_review(pull_number: int, review_id: int) -> dict[str, Any]:
    """Get a specific review on a pull request by review ID."""
    repo_config = _get_repo_config()
    if not repo_config:
        return {"success": False, "error": "No repo config found"}

    token = asyncio.run(_get_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    url = f"{_repo_url(repo_config)}/pulls/{pull_number}/reviews/{review_id}"

    async def _fetch() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=_github_headers(token))
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"GitHub API returned {response.status_code}: {response.text}",
                }
            return {"success": True, "review": response.json()}

    return asyncio.run(_fetch())


def create_pr_review(
    pull_number: int,
    body: str | None = None,
    event: str = "COMMENT",
    comments: list[dict[str, Any]] | None = None,
    commit_id: str | None = None,
) -> dict[str, Any]:
    """Create a review on a pull request.

    Args:
        pull_number: The PR number to review.
        body: The review body text (required for REQUEST_CHANGES, optional for COMMENT).
        event: The review action - one of REQUEST_CHANGES or COMMENT. APPROVE is not allowed.
        comments: Optional list of review comments. Each comment dict should have:
            - path (str): The relative file path to comment on.
            - body (str): The comment text.
            - line (int, optional): The line number in the diff to comment on.
            - side (str, optional): Which side of the diff to comment on (LEFT or RIGHT).
            - start_line (int, optional): For multi-line comments, the start line.
            - start_side (str, optional): For multi-line comments, the start side.
        commit_id: Optional SHA of the commit to review. Defaults to the latest commit.

    Returns:
        Dictionary with success status and the created review data.
    """
    if event == "APPROVE":
        return {
            "success": False,
            "error": "APPROVE is not allowed. If you think this PR should be approved, leave a COMMENT review stating that the PR looks good and should be approved.",
        }

    repo_config = _get_repo_config()
    if not repo_config:
        return {"success": False, "error": "No repo config found"}

    token = asyncio.run(_get_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    url = f"{_repo_url(repo_config)}/pulls/{pull_number}/reviews"
    payload: dict[str, Any] = {"event": event}
    if body is not None:
        payload["body"] = body
    if comments:
        payload["comments"] = comments
    if commit_id:
        payload["commit_id"] = commit_id

    async def _create() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=_github_headers(token), json=payload)
            if response.status_code not in (200, 201):
                return {
                    "success": False,
                    "error": f"GitHub API returned {response.status_code}: {response.text}",
                }
            return {"success": True, "review": response.json()}

    return asyncio.run(_create())


def update_pr_review(
    pull_number: int,
    review_id: int,
    body: str,
) -> dict[str, Any]:
    """Update the body of an existing review on a pull request.

    Args:
        pull_number: The PR number.
        review_id: The ID of the review to update.
        body: The new review body text.

    Returns:
        Dictionary with success status and the updated review data.
    """
    repo_config = _get_repo_config()
    if not repo_config:
        return {"success": False, "error": "No repo config found"}

    token = asyncio.run(_get_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    url = f"{_repo_url(repo_config)}/pulls/{pull_number}/reviews/{review_id}"

    async def _update() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.put(url, headers=_github_headers(token), json={"body": body})
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"GitHub API returned {response.status_code}: {response.text}",
                }
            return {"success": True, "review": response.json()}

    return asyncio.run(_update())


def dismiss_pr_review(
    pull_number: int,
    review_id: int,
    message: str,
) -> dict[str, Any]:
    """Dismiss a review on a pull request.

    Args:
        pull_number: The PR number.
        review_id: The ID of the review to dismiss.
        message: A message explaining why the review is being dismissed.

    Returns:
        Dictionary with success status and the dismissed review data.
    """
    repo_config = _get_repo_config()
    if not repo_config:
        return {"success": False, "error": "No repo config found"}

    token = asyncio.run(_get_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    url = f"{_repo_url(repo_config)}/pulls/{pull_number}/reviews/{review_id}/dismissals"

    async def _dismiss() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                url, headers=_github_headers(token), json={"message": message}
            )
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"GitHub API returned {response.status_code}: {response.text}",
                }
            return {"success": True, "review": response.json()}

    return asyncio.run(_dismiss())


def submit_pr_review(
    pull_number: int,
    review_id: int,
    body: str | None = None,
    event: str = "COMMENT",
) -> dict[str, Any]:
    """Submit a pending review on a pull request.

    Use this if a review was created without an event (pending state) and needs to be submitted.

    Args:
        pull_number: The PR number.
        review_id: The ID of the pending review to submit.
        body: Optional body text for the review submission.
        event: The review action - one of REQUEST_CHANGES or COMMENT. APPROVE is not allowed.

    Returns:
        Dictionary with success status and the submitted review data.
    """
    repo_config = _get_repo_config()
    if not repo_config:
        return {"success": False, "error": "No repo config found"}

    token = asyncio.run(_get_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    if event == "APPROVE":
        return {
            "success": False,
            "error": "APPROVE is not allowed. If you think this PR should be approved, leave a COMMENT review stating that the PR looks good and should be approved.",
        }

    url = f"{_repo_url(repo_config)}/pulls/{pull_number}/reviews/{review_id}/events"
    payload: dict[str, Any] = {"event": event}
    if body is not None:
        payload["body"] = body

    async def _submit() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=_github_headers(token), json=payload)
            if response.status_code not in (200, 201):
                return {
                    "success": False,
                    "error": f"GitHub API returned {response.status_code}: {response.text}",
                }
            return {"success": True, "review": response.json()}

    return asyncio.run(_submit())


def list_pr_review_comments(
    pull_number: int,
    review_id: int | None = None,
) -> dict[str, Any]:
    """List comments on a pull request review.

    Args:
        pull_number: The PR number.
        review_id: If provided, list comments for a specific review.
            If not provided, list all review comments on the PR.

    Returns:
        Dictionary with success status and the list of review comments.
    """
    repo_config = _get_repo_config()
    if not repo_config:
        return {"success": False, "error": "No repo config found"}

    token = asyncio.run(_get_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    if review_id is not None:
        url = f"{_repo_url(repo_config)}/pulls/{pull_number}/reviews/{review_id}/comments"
    else:
        url = f"{_repo_url(repo_config)}/pulls/{pull_number}/comments"

    async def _fetch() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=_github_headers(token))
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"GitHub API returned {response.status_code}: {response.text}",
                }
            return {"success": True, "comments": response.json()}

    return asyncio.run(_fetch())
