"""Linear API utilities."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from agent.utils.http import get_http_client
from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")
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

    http_client = get_http_client()
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


async def post_linear_trace_comment(issue_id: str, run_id: str, triggering_comment_id: str) -> None:
    """Post a trace URL comment on a Linear issue."""
    trace_url = get_langsmith_trace_url(run_id)
    if trace_url:
        await comment_on_linear_issue(
            issue_id,
            f"On it! [View trace]({trace_url})",
            parent_id=triggering_comment_id or None,
        )


async def list_teams() -> dict[str, Any]:
    """List all teams in the Linear workspace."""
    query = """
    query {
        teams {
            nodes {
                id
                name
                key
                description
            }
        }
    }
    """
    result = await _graphql_request(query)
    if "error" in result:
        return result
    return {"teams": result.get("teams", {}).get("nodes", [])}


async def get_issue(issue_id: str) -> dict[str, Any]:
    """Get a Linear issue by ID."""
    query = """
    query GetIssue($id: String!) {
        issue(id: $id) {
            id
            identifier
            title
            description
            priority
            priorityLabel
            state { id name }
            assignee { id name email }
            team { id name key }
            project { id name }
            labels { nodes { id name } }
            createdAt
            updatedAt
            url
        }
    }
    """
    result = await _graphql_request(query, {"id": issue_id})
    if "error" in result:
        return result
    return {"issue": result.get("issue")}


async def create_issue(
    team_id: str,
    title: str,
    description: str | None = None,
    assignee_id: str | None = None,
    priority: int | None = None,
    state_id: str | None = None,
    label_ids: list[str] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Create a new Linear issue."""
    mutation = """
    mutation IssueCreate($input: IssueCreateInput!) {
        issueCreate(input: $input) {
            success
            issue {
                id
                identifier
                title
                url
            }
        }
    }
    """
    input_vars: dict[str, Any] = {"teamId": team_id, "title": title}
    if description is not None:
        input_vars["description"] = description
    if assignee_id is not None:
        input_vars["assigneeId"] = assignee_id
    if priority is not None:
        input_vars["priority"] = priority
    if state_id is not None:
        input_vars["stateId"] = state_id
    if label_ids is not None:
        input_vars["labelIds"] = label_ids
    if project_id is not None:
        input_vars["projectId"] = project_id

    result = await _graphql_request(mutation, {"input": input_vars})
    if "error" in result:
        return result
    issue_create = result.get("issueCreate", {})
    return {
        "success": issue_create.get("success", False),
        "issue": issue_create.get("issue"),
    }


async def get_issue_comments(issue_id: str) -> dict[str, Any]:
    """Get comments for a Linear issue."""
    query = """
    query GetIssueComments($id: String!) {
        issue(id: $id) {
            comments {
                nodes {
                    id
                    body
                    createdAt
                    updatedAt
                    user { id name email }
                }
            }
        }
    }
    """
    result = await _graphql_request(query, {"id": issue_id})
    if "error" in result:
        return result
    issue = result.get("issue")
    if not issue:
        return {"error": f"Issue {issue_id} not found"}
    return {"comments": issue.get("comments", {}).get("nodes", [])}


async def update_issue(
    issue_id: str,
    title: str | None = None,
    description: str | None = None,
    assignee_id: str | None = None,
    priority: int | None = None,
    state_id: str | None = None,
    label_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing Linear issue."""
    mutation = """
    mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
        issueUpdate(id: $id, input: $input) {
            success
            issue {
                id
                identifier
                title
                url
            }
        }
    }
    """
    input_vars: dict[str, Any] = {}
    if title is not None:
        input_vars["title"] = title
    if description is not None:
        input_vars["description"] = description
    if assignee_id is not None:
        input_vars["assigneeId"] = assignee_id
    if priority is not None:
        input_vars["priority"] = priority
    if state_id is not None:
        input_vars["stateId"] = state_id
    if label_ids is not None:
        input_vars["labelIds"] = label_ids

    if not input_vars:
        return {"error": "No fields to update"}

    result = await _graphql_request(mutation, {"id": issue_id, "input": input_vars})
    if "error" in result:
        return result
    issue_update = result.get("issueUpdate", {})
    return {
        "success": issue_update.get("success", False),
        "issue": issue_update.get("issue"),
    }


async def delete_issue(issue_id: str) -> dict[str, Any]:
    """Delete a Linear issue."""
    mutation = """
    mutation IssueDelete($id: String!) {
        issueDelete(id: $id) {
            success
        }
    }
    """
    result = await _graphql_request(mutation, {"id": issue_id})
    if "error" in result:
        return result
    return {"success": result.get("issueDelete", {}).get("success", False)}
