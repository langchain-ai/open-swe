"""The transcript event taxonomy: one body per event type, plus the stored envelope.

Every event body carries its own ``type`` discriminator, so a payload read back
out of ``thread_event`` validates into exactly the model that wrote it. The
bodies are the wire format for the UI as well: they are dumped verbatim into
``thread_event.payload`` and streamed to the browser, which is why nothing here
holds bytes — an image rides as its metadata only, never as base64.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

SCHEMA_VERSION = 1
"""``thread_event.schema_version`` written by this release."""

type ActorKind = Literal["user", "agent", "system"]
type ThreadKind = Literal["agent", "reviewer"]
type ThreadStatus = Literal["idle", "running", "error"]
type MessageRole = Literal["human", "ai"]
type ToolOutcome = Literal["completed", "error"]
type NoticeKind = Literal["model_routed", "conversation_offloading", "step_limit"]
type JsonObject = dict[str, JsonValue]


class _Body(BaseModel):
    """Shared configuration for every event body."""

    model_config = ConfigDict(extra="forbid")


class MessageSender(BaseModel):
    """Who sent a human message, as the UI attributes it."""

    model_config = ConfigDict(extra="allow")

    login: str
    kind: str
    display_name: str | None = None


class MessageImage(BaseModel):
    """An image attached to a human message — metadata only, never base64."""

    model_config = ConfigDict(extra="forbid")

    mime_type: str
    file_name: str | None = None
    url: str | None = None


class ThreadCreated(_Body):
    type: Literal["thread.created"] = "thread.created"
    title: str
    kind: ThreadKind = "agent"
    source: str
    owner_login: str
    visibility: Literal["public", "private"] = "public"
    repo_owner: str | None = None
    repo_name: str | None = None
    model_id: str | None = None
    effort: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ThreadMetaPatch(BaseModel):
    """The subset of ``thread`` a ``thread.meta_updated`` event changes.

    A field left unset is left alone; ``metadata`` is merged key by key rather
    than replaced, because it mirrors LangGraph metadata that other writers own.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    status: ThreadStatus | None = None
    active_run_id: str | None = None
    metadata: JsonObject | None = None


class ThreadMetaUpdated(_Body):
    type: Literal["thread.meta_updated"] = "thread.meta_updated"
    patch: ThreadMetaPatch


class TurnRequested(_Body):
    type: Literal["turn.requested"] = "turn.requested"
    turn_id: UUID
    message_id: str
    text: str
    sender: MessageSender
    images: list[MessageImage] = Field(default_factory=list)
    model_id: str | None = None
    effort: str | None = None
    plan_mode: bool = False


class TurnStarted(_Body):
    type: Literal["turn.started"] = "turn.started"
    turn_id: UUID
    run_id: str


class TurnCompleted(_Body):
    type: Literal["turn.completed"] = "turn.completed"
    turn_id: UUID
    run_id: str | None = None
    head_commit: str | None = None
    base_commit: str | None = None
    changed_files: list[str] | None = None


class TurnFailed(_Body):
    type: Literal["turn.failed"] = "turn.failed"
    turn_id: UUID
    run_id: str | None = None
    error: str


class TurnInterrupted(_Body):
    type: Literal["turn.interrupted"] = "turn.interrupted"
    turn_id: UUID
    run_id: str | None = None


class MessageAppended(_Body):
    """A flushed fragment of an AI message.

    ``text`` and ``reasoning`` are appended to whatever the message already
    holds — the projector, the SQL upsert and the client reducer concatenate
    identically, and ``message.completed`` later replaces the accumulation with
    the canonical text so a lost fragment self-heals.
    """

    type: Literal["message.appended"] = "message.appended"
    turn_id: UUID
    message_id: str
    namespace: list[str] = Field(default_factory=list)
    text: str | None = None
    reasoning: str | None = None


class MessageCompleted(_Body):
    type: Literal["message.completed"] = "message.completed"
    turn_id: UUID
    message_id: str
    namespace: list[str] = Field(default_factory=list)
    role: MessageRole
    text: str = ""
    reasoning: str = ""
    sender: MessageSender | None = None
    images: list[MessageImage] | None = None
    created_at: datetime


class ToolStarted(_Body):
    type: Literal["tool.started"] = "tool.started"
    turn_id: UUID
    tool_call_id: str
    message_id: str | None = None
    name: str
    input: JsonObject = Field(default_factory=dict)
    namespace: list[str] = Field(default_factory=list)


class ToolCompleted(_Body):
    type: Literal["tool.completed"] = "tool.completed"
    turn_id: UUID
    tool_call_id: str
    status: ToolOutcome
    output: str = ""
    output_truncated: bool = False
    namespace: list[str] = Field(default_factory=list)


class RunNotice(_Body):
    """A live-only hint about how the run is being executed.

    Notices have no projection: the snapshot serves the latest one per kind for
    the active turn, read straight from the log.
    """

    type: Literal["run.notice"] = "run.notice"
    turn_id: UUID
    kind: NoticeKind
    data: JsonObject = Field(default_factory=dict)


type TranscriptEvent = Annotated[
    ThreadCreated
    | ThreadMetaUpdated
    | TurnRequested
    | TurnStarted
    | TurnCompleted
    | TurnFailed
    | TurnInterrupted
    | MessageAppended
    | MessageCompleted
    | ToolStarted
    | ToolCompleted
    | RunNotice,
    Field(discriminator="type"),
]


class StoredEvent(BaseModel):
    """One row of ``thread_event``, as replayed to a subscriber."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    version: int
    event_id: UUID
    event_type: str
    schema_version: int
    run_id: str | None
    turn_id: UUID | None
    command_id: str | None
    actor_kind: ActorKind
    occurred_at: datetime
    payload: JsonObject
