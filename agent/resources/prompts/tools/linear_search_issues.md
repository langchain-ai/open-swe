Search Linear issues by text, structured filters, or both.

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
