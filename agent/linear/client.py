"""Linear API client and the Linear helpers the agent tools call."""

import logging
from typing import Any

import httpx2

from agent.linear.auth import LinearAuth, linear_configured
from agent.linear.schema import (
    AgentActivityContent,
    AgentActivityCreateData,
    AgentActivitySignal,
    AgentSessionUpdateExternalUrlData,
    CommentCreateData,
    GraphQLResponse,
    IssueData,
    IssueParticipantsData,
    LinearIssue,
    ReactionCreateData,
    ViewerData,
)
from agent.utils.http import DEFAULT_HTTP_TIMEOUT
from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"

_VIEWER = """
query Viewer {
    viewer { id }
}
"""

_ISSUE_DETAILS = """
query IssueDetails($id: String!) {
    issue(id: $id) {
        id
        identifier
        title
        description
        url
        team { id name key }
        project { id name }
        creator { id name email }
        assignee { id name email }
        comments(first: 50) {
            nodes {
                id
                body
                createdAt
                user { id name email }
                botActor { id }
            }
        }
    }
}
"""

_COMMENT_CREATE = """
mutation CommentCreate($issueId: String!, $body: String!, $parentId: String) {
    commentCreate(input: { issueId: $issueId, body: $body, parentId: $parentId }) {
        success
        comment { id }
    }
}
"""

_REACTION_CREATE = """
mutation ReactionCreate($commentId: String!, $emoji: String!) {
    reactionCreate(input: { commentId: $commentId, emoji: $emoji }) {
        success
    }
}
"""

_AGENT_ACTIVITY_CREATE = """
mutation AgentActivityCreate($input: AgentActivityCreateInput!) {
    agentActivityCreate(input: $input) { success }
}
"""

_AGENT_SESSION_UPDATE_EXTERNAL_URL = """
mutation AgentSessionUpdateExternalUrl($id: String!, $url: String!) {
    agentSessionUpdateExternalUrl(id: $id, input: { externalLink: $url }) { success }
}
"""


class LinearError(Exception):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__(str(errors))
        self.errors = errors


class LinearClient:
    def __init__(self, url: str = LINEAR_API_URL) -> None:
        self._url = url
        self._http = httpx2.AsyncClient(
            auth=LinearAuth(),
            timeout=DEFAULT_HTTP_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        self._viewer_id: str | None = None

    async def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._http.post(
            self._url, json={"query": query, "variables": variables or {}}
        )
        response.raise_for_status()
        body = GraphQLResponse.model_validate(response.json())
        if body.errors:
            raise LinearError(body.errors)
        return body.data or {}

    async def viewer_id(self) -> str:
        """The actor id of the authenticated app or user, cached for the process."""
        if self._viewer_id is None:
            data = await self.execute(_VIEWER)
            self._viewer_id = ViewerData.model_validate(data).viewer.id
        return self._viewer_id

    async def get_issue(self, issue_id: str) -> LinearIssue | None:
        data = await self.execute(_ISSUE_DETAILS, {"id": issue_id})
        return IssueData.model_validate(data).issue

    async def create_comment(
        self, issue_id: str, body: str, *, parent_id: str | None = None
    ) -> str | None:
        data = await self.execute(
            _COMMENT_CREATE, {"issueId": issue_id, "body": body, "parentId": parent_id}
        )
        payload = CommentCreateData.model_validate(data).comment_create
        if not payload.success or payload.comment is None:
            return None
        return payload.comment.id

    async def react_to_comment(self, comment_id: str, emoji: str) -> bool:
        data = await self.execute(_REACTION_CREATE, {"commentId": comment_id, "emoji": emoji})
        return ReactionCreateData.model_validate(data).reaction_create.success

    async def create_agent_activity(
        self,
        session_id: str,
        content: AgentActivityContent,
        *,
        ephemeral: bool = False,
        signal: AgentActivitySignal | None = None,
    ) -> None:
        activity_input: dict[str, Any] = {
            "agentSessionId": session_id,
            "content": content.model_dump(by_alias=True, exclude_none=True),
        }
        if ephemeral:
            activity_input["ephemeral"] = True
        if signal is not None:
            activity_input["signal"] = signal
        data = await self.execute(_AGENT_ACTIVITY_CREATE, {"input": activity_input})
        payload = AgentActivityCreateData.model_validate(data).agent_activity_create
        if not payload.success:
            raise LinearError([{"message": "agentActivityCreate rejected the activity"}])

    async def update_agent_session_external_url(self, session_id: str, url: str) -> None:
        data = await self.execute(
            _AGENT_SESSION_UPDATE_EXTERNAL_URL, {"id": session_id, "url": url}
        )
        payload = AgentSessionUpdateExternalUrlData.model_validate(
            data
        ).agent_session_update_external_url
        if not payload.success:
            raise LinearError([{"message": "agentSessionUpdateExternalUrl rejected the update"}])


_client: LinearClient | None = None


def linear_client() -> LinearClient:
    global _client
    if _client is None:
        _client = LinearClient()
    return _client


async def _graphql_request(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a GraphQL request, returning ``{"error": ...}`` for the tool-facing callers."""
    if not linear_configured():
        return {"error": "LINEAR_API_KEY is not set"}
    try:
        return await linear_client().execute(query, variables)
    except LinearError as exc:
        return {"error": exc.errors}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


async def comment_on_linear_issue(
    issue_id: str, comment_body: str, parent_id: str | None = None
) -> bool:
    """Add a comment to a Linear issue, optionally as a reply to a specific comment."""
    if not linear_configured():
        return False
    try:
        comment_id = await linear_client().create_comment(
            issue_id, comment_body, parent_id=parent_id
        )
    except LinearError, httpx2.HTTPError:
        logger.warning("Linear comment failed", extra={"linear_issue": issue_id}, exc_info=True)
        return False
    return comment_id is not None


async def react_to_linear_comment(comment_id: str, emoji: str = "👀") -> bool:
    """Add an emoji reaction to a Linear comment."""
    if not linear_configured():
        return False
    try:
        return await linear_client().react_to_comment(comment_id, emoji)
    except LinearError, httpx2.HTTPError:
        logger.warning("Linear reaction failed", extra={"linear_comment": comment_id})
        return False


async def post_linear_trace_comment(
    issue_id: str, thread_id: str, triggering_comment_id: str
) -> None:
    """Post a trace URL comment on a Linear issue."""
    trace_url = await get_langsmith_trace_url(thread_id)
    body = f"On it! [View trace]({trace_url})" if trace_url else "On it!"
    await comment_on_linear_issue(issue_id, body, parent_id=triggering_comment_id or None)


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
    issue = IssueParticipantsData.model_validate(result).issue
    if issue is None:
        return None
    identities = [issue.creator, issue.assignee, *(comment.user for comment in issue.comments)]
    return {
        identity.email.strip().lower()
        for identity in identities
        if identity is not None and identity.email and identity.email.strip()
    }
