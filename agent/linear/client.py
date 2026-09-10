"""Linear API utilities."""

import logging
from typing import Any

import httpx2

from agent.config import ENV
from agent.utils.http import DEFAULT_HTTP_TIMEOUT
from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)

LINEAR_API_KEY = ENV.LINEAR_API_KEY.get()
LINEAR_API_URL = "https://api.linear.app/graphql"


def _headers() -> dict[str, str]:
    return {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json",
    }


async def _graphql_request(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a GraphQL request against the Linear API."""
    if not LINEAR_API_KEY:
        return {"error": "LINEAR_API_KEY is not set"}

    async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.post(
                LINEAR_API_URL,
                headers=_headers(),
                json={"query": query, "variables": variables or {}},
            )
            response.raise_for_status()
            result = response.json()
            if result.get("errors"):
                return {"error": result["errors"]}
            return result.get("data", {})
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}


async def comment_on_linear_issue(
    issue_id: str, comment_body: str, parent_id: str | None = None
) -> bool:
    """Add a comment to a Linear issue, optionally as a reply to a specific comment."""
    mutation = """
    mutation CommentCreate($issueId: String!, $body: String!, $parentId: String) {
        commentCreate(input: { issueId: $issueId, body: $body, parentId: $parentId }) {
            success
            comment { id }
        }
    }
    """
    result = await _graphql_request(
        mutation,
        {"issueId": issue_id, "body": comment_body, "parentId": parent_id},
    )
    return bool(result.get("commentCreate", {}).get("success"))


async def post_linear_trace_comment(
    issue_id: str, thread_id: str, triggering_comment_id: str
) -> None:
    """Post a trace URL comment on a Linear issue."""
    trace_url = await get_langsmith_trace_url(thread_id)
    if trace_url:
        await comment_on_linear_issue(
            issue_id,
            f"On it! [View trace]({trace_url})",
            parent_id=triggering_comment_id or None,
        )
    else:
        await comment_on_linear_issue(
            issue_id,
            "On it!",
            parent_id=triggering_comment_id or None,
        )


async def fetch_linear_issue_participant_emails(issue_id: str) -> set[str] | None:
    """Return verified participant emails for a Linear issue."""
    query = """
    query GetIssueParticipants($id: String!) {
        issue(id: $id) {
            creator { email }
            assignee { email }
            comments {
                nodes { user { email } }
            }
        }
    }
    """
    result = await _graphql_request(query, {"id": issue_id})
    if "error" in result:
        return None
    issue = result.get("issue")
    if not isinstance(issue, dict):
        return None
    identities = [issue.get("creator"), issue.get("assignee")]
    comments = issue.get("comments")
    if isinstance(comments, dict):
        for comment in comments.get("nodes") or []:
            if isinstance(comment, dict):
                identities.append(comment.get("user"))
    return {
        email.strip().lower()
        for identity in identities
        if isinstance(identity, dict)
        and isinstance(email := identity.get("email"), str)
        and email.strip()
    }
