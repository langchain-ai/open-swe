from typing import Any

from agent.linear.client import create_issue


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
    """Implement the `linear_create_issue` tool."""
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
