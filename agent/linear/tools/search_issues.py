from typing import Any

from agent.linear.client import search_issues


async def linear_search_issues(
    query: str | None = None,
    team_id: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 10,
    include_archived: bool = False,
    include_comments: bool = False,
    after: str | None = None,
) -> dict[str, Any]:
    """Implement the `linear_search_issues` tool."""
    return await search_issues(
        query=query,
        team_id=team_id,
        filters=filters,
        limit=limit,
        include_archived=include_archived,
        include_comments=include_comments,
        after=after,
    )
