from typing import Any

from agent.linear.client import delete_issue


async def linear_delete_issue(issue_id: str) -> dict[str, Any]:
    """Delete a Linear issue.

    Args:
        issue_id: The Linear issue UUID to delete.

    Returns:
        Dictionary with 'success' bool.
    """
    return await delete_issue(issue_id)
