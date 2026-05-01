import asyncio
from typing import Any

from ..utils.linear import delete_issue


def linear_delete_issue(issue_id: str) -> dict[str, Any]:
    """Delete a Linear issue.

    Args:
        issue_id: The Linear issue UUID to delete.

    Returns:
        Dictionary with 'success' bool.
    """
    return asyncio.run(delete_issue(issue_id))
