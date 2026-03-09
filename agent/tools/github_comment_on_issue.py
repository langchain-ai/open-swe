import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.github_pr_webhook import post_github_issue_comment
from ..utils.github_token import get_github_token


def github_comment_on_issue(message: str, issue_number: int) -> dict[str, Any]:
    """Post a comment to a GitHub issue.

    Use this tool to communicate progress and updates to stakeholders on GitHub.

    **When to use:**
    - After calling `commit_and_open_pr`, post a comment to let stakeholders know
      the task is complete and summarize what was done.
    - When answering a question or sharing an update triggered from a GitHub issue comment.

    Args:
        message: Markdown-formatted comment text to post to the GitHub issue.
        issue_number: Issue number to comment on.

    Returns:
        Dictionary with 'success' (bool) key.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    repo_config = configurable.get("repo", {})
    owner = repo_config.get("owner")
    name = repo_config.get("name")

    if not owner or not name:
        return {"success": False, "error": "Missing repo owner/name in config"}

    github_token = get_github_token()
    if not github_token:
        return {"success": False, "error": "Missing GitHub token"}

    if not message.strip():
        return {"success": False, "error": "Message cannot be empty"}

    success = asyncio.run(
        post_github_issue_comment(owner, name, issue_number, github_token, message)
    )
    return {"success": success}
