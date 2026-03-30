from typing import Any

from ..utils.linear import list_teams


async def linear_list_teams() -> dict[str, Any]:
    """List all teams in the Linear workspace.

    Returns:
        Dictionary with 'teams' list, each containing id, name, key, and description.
    """
    return await list_teams()
