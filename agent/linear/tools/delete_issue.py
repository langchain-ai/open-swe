from typing import Any

from agent.linear.client import delete_issue


async def linear_delete_issue(issue_id: str) -> dict[str, Any]:
    """Implement the `linear_delete_issue` tool."""
    return await delete_issue(issue_id)
