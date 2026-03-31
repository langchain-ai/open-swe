import asyncio
from typing import Any

from ..utils.linear import comment_on_linear_issue
from ..utils.mode import is_eval_mode


def linear_comment(comment_body: str, ticket_id: str) -> dict[str, Any]:
    """Post a comment to a Linear issue.

    Use this tool to communicate progress and completion to stakeholders on Linear.

    **When to use:**
    - After calling `commit_and_open_pr`, post a comment on the Linear ticket to let
      stakeholders know the task is complete and include the PR link. For example:
      "I've completed the implementation and opened a PR: <pr_url>"
    - When answering a question or sharing an update (no code changes needed).

    Args:
        comment_body: Markdown-formatted comment text to post to the Linear issue.
        ticket_id: The Linear issue UUID to post the comment to.

    Returns:
        Dictionary with 'success' (bool) key.
    """
    if is_eval_mode():
        return {"success": True, "intercepted": True, "comment_body": comment_body, "ticket_id": ticket_id}

    success = asyncio.run(comment_on_linear_issue(ticket_id, comment_body))
    return {"success": success}
