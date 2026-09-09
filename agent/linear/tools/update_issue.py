from typing import Any

from agent.linear.client import update_issue


async def linear_update_issue(
    issue_id: str,
    title: str | None = None,
    description: str | None = None,
    assignee_id: str | None = None,
    priority: int | None = None,
    state_id: str | None = None,
    label_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Implement the `linear_update_issue` tool."""
    return await update_issue(
        issue_id=issue_id,
        title=title,
        description=description,
        assignee_id=assignee_id,
        priority=priority,
        state_id=state_id,
        label_ids=label_ids,
    )
