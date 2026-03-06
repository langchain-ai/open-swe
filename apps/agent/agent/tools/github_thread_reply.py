import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import post_github_pr_comment


def github_thread_reply(message: str, pr_number: int) -> dict[str, Any]:
    """Post a comment to the current GitHub Pull Request.

    Use this tool to communicate progress and updates to stakeholders on GitHub.

    **When to use:**
    - After calling `commit_and_open_pr`, post a comment to let stakeholders know
      the task is complete and summarize what was done.
    - When answering a question or sharing an update triggered from a GitHub PR comment.

    Args:
        message: Markdown-formatted comment text to post to the GitHub PR.
        pr_number: Pull request number to comment on.

    Returns:
        Dictionary with 'success' (bool) key.
    """
    config = get_config()
    configurable = config.get("configurable", {})

    repo_config = configurable.get("repo", {})
    if not pr_number:
        return {"success": False, "error": "Missing pr_number argument"}
    if not repo_config:
        return {"success": False, "error": "No repo config found in config"}
    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    # Require GitHub App installation token (posts as bot)
    token = asyncio.run(get_github_app_installation_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    success = asyncio.run(post_github_pr_comment(repo_config, pr_number, message, token=token))
    return {"success": success}
