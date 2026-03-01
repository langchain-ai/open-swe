import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.linear import comment_on_linear_issue


def linear_comment(comment_body: str) -> dict[str, Any]:
    """Post a comment to the Linear issue associated with this task.

    Use this tool to communicate progress and completion to stakeholders on Linear.

    **When to use:**
    - After calling `commit_and_open_pr`, post a comment on the Linear ticket to let
      stakeholders know the task is complete and include the PR link. For example:
      "I've completed the implementation and opened a PR: <pr_url>"
    - When you need to share important updates or ask clarifying questions.

    **Tagging users:**
    - Mention Linear users with `@username` (their Linear display name or handle).
      Example: "Hey @johndoe, I've opened a PR for this — please review when you get a chance."

    Args:
        comment_body: Markdown-formatted comment text to post to the Linear issue.

    Returns:
        Dictionary with 'success' (bool) and optional 'error' (str) keys.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    linear_issue = configurable.get("linear_issue", {})
    linear_issue_id = linear_issue.get("id")

    if not linear_issue_id:
        return {"success": False, "error": "No Linear issue ID found in config"}

    success = asyncio.run(comment_on_linear_issue(linear_issue_id, comment_body))
    return {"success": success}
