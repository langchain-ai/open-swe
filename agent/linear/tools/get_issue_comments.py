from typing import Any

from agent.linear.client import get_issue_comments


async def linear_get_issue_comments(issue_id: str) -> dict[str, Any]:
    """Implement the `linear_get_issue_comments` tool."""
    return await get_issue_comments(issue_id)
