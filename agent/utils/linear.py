"""Linear API utilities."""

from __future__ import annotations

import logging
import os

import httpx

from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")


async def comment_on_linear_issue(
    issue_id: str, comment_body: str, parent_id: str | None = None
) -> bool:
    """Add a comment to a Linear issue, optionally as a reply to a specific comment.

    Args:
        issue_id: The Linear issue ID
        comment_body: The comment text
        parent_id: Optional comment ID to reply to

    Returns:
        True if successful, False otherwise
    """
    if not LINEAR_API_KEY:
        return False

    url = "https://api.linear.app/graphql"

    mutation = """
    mutation CommentCreate($issueId: String!, $body: String!, $parentId: String) {
        commentCreate(input: { issueId: $issueId, body: $body, parentId: $parentId }) {
            success
            comment {
                id
            }
        }
    }
    """

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                url,
                headers={
                    "Authorization": LINEAR_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "query": mutation,
                    "variables": {
                        "issueId": issue_id,
                        "body": comment_body,
                        "parentId": parent_id,
                    },
                },
            )
            response.raise_for_status()
            result = response.json()
            return bool(result.get("data", {}).get("commentCreate", {}).get("success"))
        except Exception:  # noqa: BLE001
            return False


async def post_linear_trace_comment(
    issue_id: str, run_id: str, triggering_comment_id: str
) -> None:
    """Post a trace URL comment on a Linear issue."""
    trace_url = await get_langsmith_trace_url(run_id)
    if trace_url:
        await comment_on_linear_issue(
            issue_id,
            f"On it! [View trace]({trace_url})",
            parent_id=triggering_comment_id or None,
        )
