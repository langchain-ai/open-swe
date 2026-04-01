import asyncio
from typing import Any

from ..utils.linear import delete_issue
from ..utils.mode import is_eval_mode


def linear_delete_issue(issue_id: str) -> dict[str, Any]:
    """Delete a Linear issue.

    Args:
        issue_id: The Linear issue UUID to delete.

    Returns:
        Dictionary with 'success' bool.
    """
    if is_eval_mode():
        return {"success": True, "intercepted": True, "issue_id": issue_id}

    return asyncio.run(delete_issue(issue_id))
