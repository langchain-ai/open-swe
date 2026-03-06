import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import post_github_pr_comment
from ..utils.github_token import get_github_token


def github_thread_reply(message: str) -> dict[str, Any]:
    """Post a comment to the current GitHub Pull Request.

    Use this tool to communicate progress and updates to stakeholders on GitHub.

    **When to use:**
    - After calling `commit_and_open_pr`, post a comment to let stakeholders know
      the task is complete and summarize what was done.
    - When answering a question or sharing an update triggered from a GitHub PR comment.

    Args:
        message: Markdown-formatted comment text to post to the GitHub PR.

    Returns:
        Dictionary with 'success' (bool) key.
    """
    config = get_config()
    configurable = config.get("configurable", {})

    repo_config = configurable.get("repo", {})
    pr_number = configurable.get("pr_number")

    if not pr_number:
        return {"success": False, "error": "No pr_number found in config"}
    if not repo_config:
        return {"success": False, "error": "No repo config found in config"}
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    # Try GitHub App installation token first (posts as bot), fall back to user OAuth token
    token = asyncio.run(get_github_app_installation_token()) or get_github_token()
    if not token:
        return {"success": False, "error": "No GitHub token found"}

    success = asyncio.run(post_github_pr_comment(repo_config, pr_number, message, token=token))
    return {"success": success}
