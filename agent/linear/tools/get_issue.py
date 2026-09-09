from typing import Any

from agent.linear.client import get_issue


async def linear_get_issue(issue_id: str) -> dict[str, Any]:
    """Implement the `linear_get_issue` tool."""
    return await get_issue(issue_id)
