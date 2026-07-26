"""Linear API utilities."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from agent.utils.langsmith import get_langsmith_trace_url

from .http import DEFAULT_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_OAUTH_URL = "https://api.linear.app/oauth/token"
_TOKEN_EXPIRY_SKEW_SECONDS = 60.0
_TOKEN_CACHE: tuple[str, float] | None = None
_WARNED_CONFIGURATIONS: set[str] = set()


class LinearAuthError(RuntimeError):
    """Safe Linear credential configuration or exchange failure."""


@dataclass(frozen=True)
class LinearAuth:
    headers: dict[str, str]
    mode: Literal["app", "legacy"]


def _monotonic() -> float:
    return time.monotonic()


def clear_linear_token_cache() -> None:
    """Drop cached Linear authentication state."""
    global _TOKEN_CACHE
    _TOKEN_CACHE = None
    _WARNED_CONFIGURATIONS.clear()


def invalidate_linear_app_token() -> None:
    global _TOKEN_CACHE
    _TOKEN_CACHE = None


def _warn_once(key: str, message: str) -> None:
    if key not in _WARNED_CONFIGURATIONS:
        logger.warning(message)
        _WARNED_CONFIGURATIONS.add(key)


def _linear_credentials() -> tuple[Literal["app", "legacy"], str, str]:
    client_id = os.environ.get("LINEAR_CLIENT_ID", "")
    client_secret = os.environ.get("LINEAR_CLIENT_SECRET", "")
    api_key = os.environ.get("LINEAR_API_KEY", "")
    has_client_id = bool(client_id.strip())
    has_client_secret = bool(client_secret.strip())

    if has_client_id != has_client_secret:
        raise LinearAuthError(
            "LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET must both be set for Linear app mode"
        )
    if has_client_id and has_client_secret:
        if api_key.strip():
            _warn_once(
                "ignored-legacy",
                "LINEAR_API_KEY is ignored because Linear app credentials are configured",
            )
        return "app", client_id, client_secret
    if api_key.strip():
        _warn_once(
            "legacy",
            "LINEAR_API_KEY authentication is deprecated; configure "
            "LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET",
        )
        return "legacy", api_key, ""
    raise LinearAuthError(
        "Configure LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET, or deprecated LINEAR_API_KEY"
    )


async def _mint_linear_app_token(client_id: str, client_secret: str) -> tuple[str, float]:
    global _TOKEN_CACHE
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
            response = await http_client.post(
                LINEAR_OAUTH_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "read,write",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise LinearAuthError("Failed to obtain Linear app token") from exc

    token = payload.get("access_token") if isinstance(payload, dict) else None
    expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
    if (
        not isinstance(token, str)
        or not token
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, int | float)
        or expires_in <= 0
    ):
        raise LinearAuthError("Failed to obtain Linear app token")

    good_until = _monotonic() + max(0.0, float(expires_in) - _TOKEN_EXPIRY_SKEW_SECONDS)
    _TOKEN_CACHE = (token, good_until)
    return token, good_until


async def get_linear_auth() -> LinearAuth:
    """Resolve the configured Linear authorization header."""
    mode, credential, secret = _linear_credentials()
    if mode == "legacy":
        return LinearAuth(headers={"Authorization": credential}, mode=mode)

    cached = _TOKEN_CACHE
    if cached is not None and _monotonic() < cached[1]:
        token = cached[0]
    else:
        invalidate_linear_app_token()
        token, _ = await _mint_linear_app_token(credential, secret)
    return LinearAuth(headers={"Authorization": f"Bearer {token}"}, mode=mode)


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    return value


async def _graphql_request(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a GraphQL request against the Linear API."""
    try:
        auth = await get_linear_auth()
    except LinearAuthError as exc:
        return {"error": str(exc)}

    payload = {"query": query, "variables": variables or {}}
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
                response = await http_client.post(
                    LINEAR_API_URL,
                    headers={**auth.headers, "Content-Type": "application/json"},
                    json=payload,
                )
        except Exception:  # noqa: BLE001
            return {"error": "Linear API request failed"}

        if response.status_code == 401 and auth.mode == "app" and attempt == 0:
            invalidate_linear_app_token()
            try:
                auth = await get_linear_auth()
            except LinearAuthError as exc:
                return {"error": str(exc)}
            continue
        if response.status_code >= 400:
            return {"error": f"Linear API request failed with status {response.status_code}"}

        try:
            result = response.json()
        except ValueError:
            return {"error": "Linear API returned an invalid response"}
        if not isinstance(result, dict):
            return {"error": "Linear API returned an invalid response"}
        if result.get("errors"):
            secret_values = tuple(
                value
                for value in (
                    os.environ.get("LINEAR_CLIENT_SECRET", ""),
                    auth.headers.get("Authorization", "").removeprefix("Bearer "),
                )
                if value
            )
            return {"error": _redact(result["errors"], secret_values)}
        data = result.get("data", {})
        return data if isinstance(data, dict) else {}

    return {"error": "Linear API request failed with status 401"}


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
    trace_url = get_langsmith_trace_url(thread_id)
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


async def search_issues(
    query: str | None = None,
    team_id: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 10,
    include_archived: bool = False,
    include_comments: bool = False,
    after: str | None = None,
) -> dict[str, Any]:
    """Search Linear issues by text, structured filters, or both."""
    query = (query or "").strip()
    issue_filter = dict(filters or {})
    if team_id:
        team_filter = {"team": {"id": {"eq": team_id}}}
        issue_filter = {"and": [issue_filter, team_filter]} if issue_filter else team_filter
    if not query and not issue_filter:
        return {"error": "Search query or filters must be provided"}
    if not 1 <= limit <= 50:
        return {"error": "Search limit must be between 1 and 50"}

    connection_fields = """
        totalCount
        pageInfo {
            hasNextPage
            endCursor
        }
        nodes {
            id
            identifier
            title
            priority
            priorityLabel
            state { id name type }
            assignee { id name email }
            team { id name key }
            project { id name }
            labels { nodes { id name } }
            createdAt
            updatedAt
            archivedAt
            url
        }
    """
    if query:
        graphql_query = f"""
        query SearchIssues(
            $query: String!
            $filter: IssueFilter
            $limit: Int!
            $includeArchived: Boolean
            $includeComments: Boolean
            $after: String
        ) {{
            searchIssues(
                term: $query
                filter: $filter
                first: $limit
                includeArchived: $includeArchived
                includeComments: $includeComments
                after: $after
            ) {{
                {connection_fields}
            }}
        }}
        """
        variables = {
            "query": query,
            "filter": issue_filter or None,
            "limit": limit,
            "includeArchived": include_archived,
            "includeComments": include_comments,
            "after": after,
        }
        connection_name = "searchIssues"
    else:
        graphql_query = f"""
        query FilterIssues(
            $filter: IssueFilter!
            $limit: Int!
            $includeArchived: Boolean
            $after: String
        ) {{
            issues(
                filter: $filter
                first: $limit
                includeArchived: $includeArchived
                after: $after
            ) {{
                {connection_fields}
            }}
        }}
        """
        variables = {
            "filter": issue_filter,
            "limit": limit,
            "includeArchived": include_archived,
            "after": after,
        }
        connection_name = "issues"

    result = await _graphql_request(graphql_query, variables)
    if "error" in result:
        return result

    search_results = result.get(connection_name, {})
    return {
        "issues": search_results.get("nodes", []),
        "total_count": search_results.get("totalCount", 0),
        "page_info": search_results.get("pageInfo", {}),
    }


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
