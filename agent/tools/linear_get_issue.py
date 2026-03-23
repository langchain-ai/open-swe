import asyncio
from typing import Any

from ..utils.linear import get_issue


def linear_get_issue(issue_id: str) -> dict[str, Any]:
    """Get a Linear issue by its ID.

    Args:
        issue_id: The Linear issue UUID.

    Returns:
        Dictionary with 'issue' containing full issue details.
    """
    return asyncio.run(get_issue(issue_id))
