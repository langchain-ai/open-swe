from typing import Any

from ..utils.linear import search_issues


async def linear_search_issues(
    query: str | None = None,
    team_id: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 10,
    include_archived: bool = False,
    include_comments: bool = False,
    after: str | None = None,
) -> dict[str, Any]:
    """Search Linear issues by text, structured filters, or both.

    Args:
        query: Optional free-text query over issue content.
        team_id: Optional team UUID used to restrict matches to that team.
        filters: Optional Linear IssueFilter object for labels, state, project, assignee, and more.
        limit: Maximum results to return, from 1 to 50.
        include_archived: Whether to include archived issues.
        include_comments: Whether free-text search includes issue comments.
        after: Optional pagination cursor from a previous result's page_info.endCursor.

    Returns:
        Matching issues plus total_count and page_info for pagination.
    """
    return await search_issues(
        query=query,
        team_id=team_id,
        filters=filters,
        limit=limit,
        include_archived=include_archived,
        include_comments=include_comments,
        after=after,
    )
