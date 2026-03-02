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
    - When answering a question or sharing an update (no code changes needed).

    **Tagging users:**
    - The name of the person who triggered this task is available as `triggering_user_name`
      in the linear_issue config. Use `@{triggering_user_name}` to mention them.
      Example: if triggering_user_name is "Yogesh Mahendran", tag them as "@Yogesh Mahendran".

    Args:
        comment_body: Markdown-formatted comment text to post to the Linear issue.

    Returns:
        Dictionary with 'success' (bool), 'triggering_user_name' (str), and optional 'error' (str) keys.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    linear_issue = configurable.get("linear_issue", {})
    linear_issue_id = linear_issue.get("id")
    triggering_user_name = linear_issue.get("triggering_user_name", "")

    if not linear_issue_id:
        return {"success": False, "error": "No Linear issue ID found in config"}

    success = asyncio.run(comment_on_linear_issue(linear_issue_id, comment_body))
    return {"success": success, "triggering_user_name": triggering_user_name}
