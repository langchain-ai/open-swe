Update an existing Linear issue.

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
