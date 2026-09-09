Create a new Linear issue.

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
