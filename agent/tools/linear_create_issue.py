from typing import Any

from ..utils.linear import create_issue


async def linear_create_issue(
    team_id: str,
    title: str,
    description: str | None = None,
    assignee_id: str | None = None,
    priority: int | None = None,
    state_id: str | None = None,
    label_ids: list[str] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Create a new Linear issue.

    Args:
        team_id: The ID of the team to create the issue in.
        title: The title of the issue.
        description: Optional markdown description.
        assignee_id: Optional user ID to assign the issue to.
        priority: Optional priority (0=none, 1=urgent, 2=high, 3=medium, 4=low).
        state_id: Optional workflow state ID.
        label_ids: Optional list of label IDs to apply.
        project_id: Optional project ID to associate with.

    Returns:
        Dictionary with 'success' bool and 'issue' details.
    """
    return await create_issue(
        team_id=team_id,
        title=title,
        description=description,
        assignee_id=assignee_id,
        priority=priority,
        state_id=state_id,
        label_ids=label_ids,
        project_id=project_id,
    )
