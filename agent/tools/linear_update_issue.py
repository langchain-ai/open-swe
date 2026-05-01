import asyncio
from typing import Any

from ..utils.linear import update_issue


def linear_update_issue(
    issue_id: str,
    title: str | None = None,
    description: str | None = None,
    assignee_id: str | None = None,
    priority: int | None = None,
    state_id: str | None = None,
    label_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing Linear issue.

    Args:
        issue_id: The Linear issue UUID to update.
        title: New title for the issue.
        description: New markdown description.
        assignee_id: User ID to assign the issue to.
        priority: Priority (0=none, 1=urgent, 2=high, 3=medium, 4=low).
        state_id: Workflow state ID to transition to.
        label_ids: List of label IDs to set.

    Returns:
        Dictionary with 'success' bool and updated 'issue' details.
    """
    return asyncio.run(
        update_issue(
            issue_id=issue_id,
            title=title,
            description=description,
            assignee_id=assignee_id,
            priority=priority,
            state_id=state_id,
            label_ids=label_ids,
        )
    )
