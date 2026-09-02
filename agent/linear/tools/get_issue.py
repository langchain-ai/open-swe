from typing import Any

from agent.linear.client import get_issue


async def linear_get_issue(issue_id: str) -> dict[str, Any]:
    """Get a Linear issue by its ID.

    Args:
        issue_id: The Linear issue UUID.

    Returns:
        Dictionary with 'issue' containing full issue details.
    """
    return await get_issue(issue_id)
