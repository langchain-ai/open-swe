import asyncio
from typing import Any

from ..utils.linear import get_issue_comments


def linear_get_issue_comments(issue_id: str) -> dict[str, Any]:
    """Get all comments on a Linear issue.

    Args:
        issue_id: The Linear issue UUID.

    Returns:
        Dictionary with 'comments' list, each containing id, body, createdAt, user, etc.
    """
    return asyncio.run(get_issue_comments(issue_id))
