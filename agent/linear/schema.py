"""Typed Linear payloads: webhook envelopes, GraphQL responses, agent-session activities."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _LinearModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


def _connection_nodes(value: Any) -> Any:
    """Flatten a GraphQL ``{nodes: [...]}`` connection into a plain list."""
    if isinstance(value, dict):
        return value.get("nodes") or []
    if value is None:
        return []
    return value


def _optional_connection_nodes(value: Any) -> Any:
    return None if value is None else _connection_nodes(value)


class LinearUser(_LinearModel):
    id: str | None = None
    name: str | None = None
    email: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")


class LinearTeam(_LinearModel):
    id: str | None = None
    name: str | None = None
    key: str | None = None


class LinearProject(_LinearModel):
    id: str | None = None
    name: str | None = None


class LinearComment(_LinearModel):
    id: str
    body: str = ""
    created_at: str | None = Field(default=None, alias="createdAt")
    user: LinearUser | None = None
    issue_id: str | None = Field(default=None, alias="issueId")
    # Quoted so pydantic defers this cycle instead of evaluating it during class creation.
    issue: "LinearIssue | None" = None  # noqa: UP037
    bot_actor: dict[str, Any] | None = Field(default=None, alias="botActor")
    parent_id: str | None = Field(default=None, alias="parentId")


class LinearIssue(_LinearModel):
    id: str
    identifier: str | None = None
    title: str | None = None
    description: str | None = None
    url: str | None = None
    team: LinearTeam | None = None
    project: LinearProject | None = None
    creator: LinearUser | None = None
    assignee: LinearUser | None = None
    comments: list[LinearComment] = []

    @field_validator("comments", mode="before")
    @classmethod
    def _flatten_comments(cls, value: Any) -> Any:
        return _connection_nodes(value)


LinearComment.model_rebuild()


class LinearWebhookEnvelope(_LinearModel):
    type: str
    action: str
    webhook_timestamp: int = Field(alias="webhookTimestamp")
    created_at: str | None = Field(default=None, alias="createdAt")
    organization_id: str | None = Field(default=None, alias="organizationId")
    webhook_id: str | None = Field(default=None, alias="webhookId")


class LinearWebhookActor(_LinearModel):
    id: str | None = None
    type: str | None = None
    name: str | None = None
    email: str | None = None


class CommentCreateEvent(LinearWebhookEnvelope):
    data: LinearComment
    actor: LinearWebhookActor | None = None


AgentSessionStatus = Literal["pending", "active", "awaitingInput", "error", "complete", "stale"]
AgentActivityType = Literal["prompt", "thought", "action", "elicitation", "response", "error"]
AgentActivitySignal = Literal["auth", "continue", "select", "stop"]


class ThoughtContent(_LinearModel):
    type: Literal["thought"] = "thought"
    body: str


class ActionContent(_LinearModel):
    type: Literal["action"] = "action"
    action: str
    parameter: str
    result: str | None = None


class ElicitationContent(_LinearModel):
    type: Literal["elicitation"] = "elicitation"
    body: str


class ResponseContent(_LinearModel):
    type: Literal["response"] = "response"
    body: str


class ErrorContent(_LinearModel):
    type: Literal["error"] = "error"
    body: str
    reason_code: str | None = Field(default=None, alias="reasonCode")


class PromptContent(_LinearModel):
    type: Literal["prompt"] = "prompt"
    body: str


AgentActivityContent = Annotated[
    ThoughtContent
    | ActionContent
    | ElicitationContent
    | ResponseContent
    | ErrorContent
    | PromptContent,
    Field(discriminator="type"),
]


class AgentActivity(_LinearModel):
    id: str | None = None
    content: AgentActivityContent
    signal: str | None = None
    ephemeral: bool = False
    created_at: str | None = Field(default=None, alias="createdAt")
    source_comment_id: str | None = Field(default=None, alias="sourceCommentId")
    user: LinearUser | None = None


class AgentSession(_LinearModel):
    id: str
    status: str
    issue: LinearIssue | None = None
    comment: LinearComment | None = None
    source_comment: LinearComment | None = Field(default=None, alias="sourceComment")
    creator: LinearUser | None = None
    app_user: LinearUser | None = Field(default=None, alias="appUser")
    app_user_id: str | None = Field(default=None, alias="appUserId")
    external_link: str | None = Field(default=None, alias="externalLink")
    created_at: str | None = Field(default=None, alias="createdAt")


class GuidanceRule(_LinearModel):
    body: str


class AgentSessionEvent(LinearWebhookEnvelope):
    action: Literal["created", "prompted"]
    agent_session: AgentSession = Field(alias="agentSession")
    agent_activity: AgentActivity | None = Field(default=None, alias="agentActivity")
    prompt_context: str | None = Field(default=None, alias="promptContext")
    guidance: list[GuidanceRule] = []
    previous_comments: list[LinearComment] = Field(default=[], alias="previousComments")
    oauth_client_id: str | None = Field(default=None, alias="oauthClientId")
    app_user_id: str | None = Field(default=None, alias="appUserId")


def parse_linear_webhook(
    raw: bytes,
) -> CommentCreateEvent | AgentSessionEvent | LinearWebhookEnvelope:
    """Narrow a raw Linear webhook body to the most specific model its ``type`` supports."""
    envelope = LinearWebhookEnvelope.model_validate_json(raw)
    if envelope.type == "Comment":
        return CommentCreateEvent.model_validate_json(raw)
    if envelope.type == "AgentSessionEvent":
        return AgentSessionEvent.model_validate_json(raw)
    return envelope


class GraphQLResponse(_LinearModel):
    data: dict[str, Any] | None = None
    errors: list[dict[str, Any]] | None = None


class Viewer(_LinearModel):
    id: str


class ViewerData(_LinearModel):
    viewer: Viewer


class CommentRef(_LinearModel):
    id: str


class CommentCreatePayload(_LinearModel):
    success: bool = False
    comment: CommentRef | None = None


class CommentCreateData(_LinearModel):
    comment_create: CommentCreatePayload = Field(alias="commentCreate")


class SuccessPayload(_LinearModel):
    success: bool = False


class ReactionCreateData(_LinearModel):
    reaction_create: SuccessPayload = Field(alias="reactionCreate")


class AgentActivityCreateData(_LinearModel):
    agent_activity_create: SuccessPayload = Field(alias="agentActivityCreate")


class AgentSessionUpdateExternalUrlData(_LinearModel):
    agent_session_update_external_url: SuccessPayload = Field(alias="agentSessionUpdateExternalUrl")


class IssueData(_LinearModel):
    issue: LinearIssue | None = None


class LabelNode(_LinearModel):
    id: str | None = None
    name: str | None = None


class WorkflowState(_LinearModel):
    id: str | None = None
    name: str | None = None
    type: str | None = None


class IssueSummary(_LinearModel):
    id: str | None = None
    identifier: str | None = None
    title: str | None = None
    description: str | None = None
    priority: int | None = None
    priority_label: str | None = Field(default=None, alias="priorityLabel")
    state: WorkflowState | None = None
    assignee: LinearUser | None = None
    team: LinearTeam | None = None
    project: LinearProject | None = None
    labels: list[LabelNode] | None = None
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")
    archived_at: str | None = Field(default=None, alias="archivedAt")
    url: str | None = None

    @field_validator("labels", mode="before")
    @classmethod
    def _flatten_labels(cls, value: Any) -> Any:
        return _optional_connection_nodes(value)


class IssueSummaryData(_LinearModel):
    issue: IssueSummary | None = None


class TeamNode(_LinearModel):
    id: str
    name: str | None = None
    key: str | None = None
    description: str | None = None


class TeamsConnection(_LinearModel):
    nodes: list[TeamNode] = []


class TeamsData(_LinearModel):
    teams: TeamsConnection


class PageInfo(_LinearModel):
    has_next_page: bool | None = Field(default=None, alias="hasNextPage")
    end_cursor: str | None = Field(default=None, alias="endCursor")


class IssueSearchConnection(_LinearModel):
    nodes: list[IssueSummary] = []
    total_count: int = Field(default=0, alias="totalCount")
    page_info: PageInfo = Field(default_factory=lambda: PageInfo(), alias="pageInfo")


class IssueSearchData(_LinearModel):
    search_issues: IssueSearchConnection | None = Field(default=None, alias="searchIssues")
    issues: IssueSearchConnection | None = None


class IssueMutationRef(_LinearModel):
    id: str | None = None
    identifier: str | None = None
    title: str | None = None
    url: str | None = None


class IssueMutationPayload(_LinearModel):
    success: bool = False
    issue: IssueMutationRef | None = None


class IssueCreateData(_LinearModel):
    issue_create: IssueMutationPayload = Field(alias="issueCreate")


class IssueUpdateData(_LinearModel):
    issue_update: IssueMutationPayload = Field(alias="issueUpdate")


class IssueDeleteData(_LinearModel):
    issue_delete: SuccessPayload = Field(alias="issueDelete")


class IssueCommentsConnection(_LinearModel):
    nodes: list[LinearComment] = []


class IssueWithComments(_LinearModel):
    comments: IssueCommentsConnection = Field(default_factory=lambda: IssueCommentsConnection())


class IssueCommentsData(_LinearModel):
    issue: IssueWithComments | None = None


class IssueParticipant(_LinearModel):
    email: str | None = None


class IssueParticipantComment(_LinearModel):
    user: IssueParticipant | None = None


class IssueParticipants(_LinearModel):
    creator: IssueParticipant | None = None
    assignee: IssueParticipant | None = None
    comments: list[IssueParticipantComment] = []

    @field_validator("comments", mode="before")
    @classmethod
    def _flatten_comments(cls, value: Any) -> Any:
        return _connection_nodes(value)


class IssueParticipantsData(_LinearModel):
    issue: IssueParticipants | None = None
