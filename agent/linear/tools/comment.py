from typing import Any

from agent.linear.client import comment_on_linear_issue


async def linear_comment(comment_body: str, ticket_id: str) -> dict[str, Any]:
    """Implement the `linear_comment` tool."""
    success = await comment_on_linear_issue(ticket_id, comment_body)
    return {"success": success}
