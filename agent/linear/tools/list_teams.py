from typing import Any

from agent.linear.client import list_teams


async def linear_list_teams() -> dict[str, Any]:
    """Implement the `linear_list_teams` tool."""
    return await list_teams()
