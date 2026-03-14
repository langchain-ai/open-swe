import asyncio
import logging
from typing import Any

from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import fetch_pr_comments_since_last_tag
from ..utils.github_token import get_github_token

logger = logging.getLogger(__name__)


def fetch_github_pr_comments(pr_number: int) -> dict[str, Any]:
    """Fetch all review comments on a GitHub pull request.

    Use this tool when you need to read PR review comments, inline code review
    comments, or general PR discussion to understand what changes are requested.
    This is especially useful when triggered from Slack and the PR comments were
    not included in the initial context.

    Args:
        pr_number: The pull request number to fetch comments for.

    Returns:
        Dictionary containing:
        - success: Whether the operation completed successfully
        - comments: List of comment dicts with 'author', 'body', 'type', and optionally 'path'/'line'
        - formatted: Human-readable string of all comments, ready to read
        - error: Error string if something failed, otherwise None
    """
    try:
        config = get_config()
        configurable = config.get("configurable", {})
        repo_config = configurable.get("repo", {})
        if not repo_config:
            return {"success": False, "error": "No repo config found in config", "comments": [], "formatted": ""}

        token = get_github_token()
        if not token:
            token = asyncio.run(get_github_app_installation_token())
        if not token:
            return {"success": False, "error": "No GitHub token available", "comments": [], "formatted": ""}

        comments = asyncio.run(fetch_pr_comments_since_last_tag(repo_config, pr_number, token=token))

        if not comments:
            return {
                "success": True,
                "error": None,
                "comments": [],
                "formatted": "No PR comments found since the last @open-swe tag.",
            }

        lines = []
        for c in comments:
            author = c.get("author", "unknown")
            body = c.get("body", "")
            comment_type = c.get("type", "")
            if comment_type == "review_comment":
                path = c.get("path", "")
                line = c.get("line", "")
                loc = f" (file: `{path}`, line: {line})" if path else ""
                lines.append(f"\n**{author}**{loc}:\n{body}\n")
            else:
                lines.append(f"\n**{author}**:\n{body}\n")

        formatted = "".join(lines)
        return {"success": True, "error": None, "comments": comments, "formatted": formatted}
    except Exception as e:
        logger.exception("fetch_github_pr_comments failed")
        return {"success": False, "error": f"{type(e).__name__}: {e}", "comments": [], "formatted": ""}
