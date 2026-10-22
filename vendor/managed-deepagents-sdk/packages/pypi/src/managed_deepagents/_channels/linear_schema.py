"""Receipt and dispatch schemas for handwritten Linear webhook payload subsets."""

from collections.abc import Callable
from typing import Annotated, Literal, NotRequired, overload

from pydantic import BeforeValidator, ConfigDict, TypeAdapter, with_config
from typing_extensions import TypedDict, TypeIs

from managed_deepagents._channels.scalars import Nonblank


@with_config(ConfigDict(extra="allow", strict=True))
class _ReceiptPayload(TypedDict):
    type: Nonblank
    action: Nonblank
    organizationId: NotRequired[str]


class LinearReceipt(TypedDict):
    delivery_id: Nonblank
    payload: _ReceiptPayload


linear_receipt_adapter = TypeAdapter(LinearReceipt)

# Dispatch schemas are stronger than the receipt schema, and are used only when
# a matching handler is registered. Unknown provider fields are kept at every level.


@with_config(ConfigDict(extra="allow", strict=True))
class LinearTeamPayload(TypedDict):
    id: Nonblank
    key: Nonblank
    name: NotRequired[str]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearActorPayload(TypedDict):
    id: Nonblank
    type: NotRequired[str]
    name: NotRequired[str]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearLabel(TypedDict):
    id: str
    name: str


@with_config(ConfigDict(extra="allow", strict=True))
class LinearIssue(TypedDict):
    id: Nonblank
    identifier: Nonblank
    title: str
    description: NotRequired[str | None]
    url: NotRequired[str]
    teamId: NotRequired[str]
    team: NotRequired[LinearTeamPayload]
    labelIds: NotRequired[list[str]]
    labels: NotRequired[list[LinearLabel]]
    botActor: NotRequired[object]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearCommentUser(TypedDict):
    id: str
    name: NotRequired[str]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearCommentIssue(TypedDict):
    id: Nonblank
    identifier: NotRequired[str]
    title: NotRequired[str]
    url: NotRequired[str]
    team: NotRequired[LinearTeamPayload]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearComment(TypedDict):
    id: Nonblank
    body: str
    issueId: Nonblank
    parentId: NotRequired[str | None]
    userId: NotRequired[str | None]
    user: NotRequired[LinearCommentUser | None]
    issue: NotRequired[LinearCommentIssue]
    botActor: NotRequired[object]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearSessionIssue(TypedDict):
    id: Nonblank
    identifier: NotRequired[str]
    title: NotRequired[str]
    url: NotRequired[str]
    description: NotRequired[str | None]
    team: NotRequired[LinearTeamPayload]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearSessionComment(TypedDict):
    id: Nonblank
    body: NotRequired[str]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearPerson(TypedDict):
    id: Nonblank
    name: NotRequired[str]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearAgentSession(TypedDict):
    """A Linear agent session: the app was mentioned or delegated an issue."""

    id: Nonblank
    status: NotRequired[str]
    url: NotRequired[str]
    issueId: NotRequired[str | None]
    issue: NotRequired[LinearSessionIssue | None]
    commentId: NotRequired[str | None]
    """The comment that started the session, when a mention did."""
    comment: NotRequired[LinearSessionComment | None]
    creatorId: NotRequired[str | None]
    creator: NotRequired[LinearPerson | None]


LINEAR_ACTIVITY_TYPES = frozenset(
    {"prompt", "thought", "action", "response", "error", "elicitation"}
)
"""The activity content types Linear documents. A user's reply is a ``prompt``."""
LINEAR_PROMPT_SIGNALS = frozenset({"stop"})
"""The signals Linear documents on a user's activity. ``stop`` asks the agent to stop."""


def _known(values: frozenset[str]) -> Callable[[object], object]:
    """Read a string Linear may add values to later as ``unknown``."""

    def known(value: object) -> object:
        if isinstance(value, str) and value not in values:
            return "unknown"
        return value

    return known


LinearActivityType = Annotated[
    Literal["prompt", "thought", "action", "response", "error", "elicitation", "unknown"],
    BeforeValidator(_known(LINEAR_ACTIVITY_TYPES)),
]
LinearPromptSignal = Annotated[
    Literal["stop", "unknown"], BeforeValidator(_known(LINEAR_PROMPT_SIGNALS))
]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearActivityContent(TypedDict):
    type: LinearActivityType
    """A content type Linear adds later reads as ``unknown``."""
    body: NotRequired[str]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearAgentActivity(TypedDict):
    """What a user said in a session: a reply, or a signal such as ``stop``."""

    content: LinearActivityContent
    signal: NotRequired[LinearPromptSignal | None]
    """A signal Linear adds later reads as ``unknown``."""
    signalMetadata: NotRequired[object]
    sourceCommentId: NotRequired[str | None]
    userId: NotRequired[str | None]
    user: NotRequired[LinearPerson | None]


@with_config(ConfigDict(extra="allow", strict=True))
class _Envelope(TypedDict):
    organizationId: Nonblank
    webhookTimestamp: int | float
    webhookId: NotRequired[str]
    createdAt: NotRequired[str]
    url: NotRequired[str]
    actor: NotRequired[LinearActorPayload]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearIssueCreatePayload(_Envelope):
    type: Literal["Issue"]
    action: Literal["create"]
    data: LinearIssue


@with_config(ConfigDict(extra="allow", strict=True))
class LinearIssueUpdatePayload(_Envelope):
    type: Literal["Issue"]
    action: Literal["update"]
    data: LinearIssue
    updatedFrom: NotRequired[dict[str, object]]
    """The changed fields' previous values."""


@with_config(ConfigDict(extra="allow", strict=True))
class LinearCommentCreatePayload(_Envelope):
    type: Literal["Comment"]
    action: Literal["create"]
    data: LinearComment


@with_config(ConfigDict(extra="allow", strict=True))
class LinearSessionCreatedPayload(_Envelope):
    type: Literal["AgentSessionEvent"]
    action: Literal["created"]
    appUserId: NotRequired[str]
    agentSession: LinearAgentSession
    promptContext: NotRequired[str | None]
    """Linear's ready-made prompt: the issue, the thread and who asked."""
    guidance: NotRequired[object]


@with_config(ConfigDict(extra="allow", strict=True))
class LinearSessionPromptedPayload(_Envelope):
    type: Literal["AgentSessionEvent"]
    action: Literal["prompted"]
    appUserId: NotRequired[str]
    agentSession: LinearAgentSession
    agentActivity: LinearAgentActivity


LinearPayload = (
    LinearIssueCreatePayload
    | LinearIssueUpdatePayload
    | LinearCommentCreatePayload
    | LinearSessionCreatedPayload
    | LinearSessionPromptedPayload
)
LinearEventKey = Literal[
    "Issue.create",
    "Issue.update",
    "Comment.create",
    "AgentSessionEvent.created",
    "AgentSessionEvent.prompted",
]

_PAYLOAD_ADAPTERS: dict[LinearEventKey, TypeAdapter[LinearPayload]] = {
    "Issue.create": TypeAdapter(LinearIssueCreatePayload),
    "Issue.update": TypeAdapter(LinearIssueUpdatePayload),
    "Comment.create": TypeAdapter(LinearCommentCreatePayload),
    "AgentSessionEvent.created": TypeAdapter(LinearSessionCreatedPayload),
    "AgentSessionEvent.prompted": TypeAdapter(LinearSessionPromptedPayload),
}
SESSION_EVENT_KEYS: frozenset[LinearEventKey] = frozenset(
    {"AgentSessionEvent.created", "AgentSessionEvent.prompted"}
)
SUPPORTED_EVENT_KEYS = frozenset(_PAYLOAD_ADAPTERS)


@overload
def validate_payload(key: Literal["Issue.create"], payload: object) -> LinearIssueCreatePayload: ...
@overload
def validate_payload(key: Literal["Issue.update"], payload: object) -> LinearIssueUpdatePayload: ...
@overload
def validate_payload(
    key: Literal["Comment.create"], payload: object
) -> LinearCommentCreatePayload: ...
@overload
def validate_payload(
    key: Literal["AgentSessionEvent.created"], payload: object
) -> LinearSessionCreatedPayload: ...
@overload
def validate_payload(
    key: Literal["AgentSessionEvent.prompted"], payload: object
) -> LinearSessionPromptedPayload: ...
def validate_payload(key: LinearEventKey, payload: object) -> LinearPayload:
    """Validate a delivery's payload against the schema of its event key."""
    return _PAYLOAD_ADAPTERS[key].validate_python(payload, strict=True)


def is_event_key(value: object) -> TypeIs[LinearEventKey]:
    """Whether ``value`` is a supported event key."""
    return value in SUPPORTED_EVENT_KEYS
