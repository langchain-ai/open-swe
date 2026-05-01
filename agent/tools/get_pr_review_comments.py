import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import _fetch_paginated


def get_pr_review_comments(
    pr_number: int,
    repo_owner: str | None = None,
    repo_name: str | None = None,
) -> dict[str, Any]:
    """Fetch all review comments for a GitHub pull request.

    Returns thread comments, inline review comments, and review submissions
    sorted chronologically.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    repo_config = configurable.get("repo", {})

    owner = repo_owner or repo_config.get("owner", "")
    repo = repo_name or repo_config.get("name", "")

    if not owner or not repo:
        return {
            "success": False,
            "error": "No repo config found — provide repo_owner/repo_name or set repo in config",
        }

    token = asyncio.run(get_github_app_installation_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    base = f"https://api.github.com/repos/{owner}/{repo}"

    async def _fetch_all() -> list[dict[str, Any]]:
        import httpx

        async with httpx.AsyncClient() as http_client:
            pr_comments, review_comments, reviews = await asyncio.gather(
                _fetch_paginated(http_client, f"{base}/issues/{pr_number}/comments", headers),
                _fetch_paginated(http_client, f"{base}/pulls/{pr_number}/comments", headers),
                _fetch_paginated(http_client, f"{base}/pulls/{pr_number}/reviews", headers),
            )
        all_comments: list[dict[str, Any]] = []

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

        all_comments.sort(key=lambda c: c.get("created_at", ""))
        return all_comments

    comments = asyncio.run(_fetch_all())
    return {"success": True, "pr_number": pr_number, "total": len(comments), "comments": comments}
