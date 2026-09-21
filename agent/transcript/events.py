"""The transcript event taxonomy: one body per event type, plus the stored envelope.

Every event body carries its own ``type`` discriminator, so a payload read back
out of ``thread_event`` validates into exactly the model that wrote it. The
bodies are the wire format for the UI as well: they are dumped verbatim into
``thread_event.payload`` and streamed to the browser, which is why nothing here
holds bytes — an attachment rides as its metadata only, never as base64.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
)

SCHEMA_VERSION = 1
"""``thread_event.schema_version`` written by this release."""

type ActorKind = Literal["user", "agent", "system"]
type ThreadStatus = Literal["idle", "running", "error"]
type MessageRole = Literal["human", "ai"]
type ToolOutcome = Literal["completed", "error"]
type NoticeKind = Literal["model_routed", "conversation_offloading"]
type CheckpointStatus = Literal["ready", "missing", "error"]
type FileChangeKind = Literal["added", "removed", "modified"]
type JsonObject = dict[str, JsonValue]

TOOL_OUTPUT_PREVIEW_CHARS = 2000
"""How much of a tool's output rides on the wire; the rest is fetched on demand."""


class _Body(BaseModel):
    """Shared configuration for every event body."""

    model_config = ConfigDict(extra="forbid")


class MessageSender(BaseModel):
    """Who sent a human message, as the UI attributes it."""

    model_config = ConfigDict(extra="allow")

    login: str
    kind: str
    display_name: str | None = None


class MessageAttachment(BaseModel):
    """A file attached to a message — metadata only, never base64.

    ``attachment_id`` addresses the bytes, which are stored in
    ``thread_attachment`` by the same transaction that appended the event and
    are served by
    ``GET /dashboard/api/threads/{thread_id}/transcript/attachments/{attachment_id}``.
    It is unset only for an attachment whose bytes were not captured.
    """

    model_config = ConfigDict(extra="forbid")

    mime_type: str
    file_name: str | None = None
    url: str | None = None
    attachment_id: UUID | None = None


class MessageUsage(BaseModel):
    """Token accounting for one AI message, as the provider reported it.

    The UI reads context usage as ``input_tokens + output_tokens`` of the
    newest AI message, falling back to ``total_tokens``.
    """

    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ThreadCreated(_Body):
    type: Literal["thread.created"] = "thread.created"
    title: str
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
    metadata: JsonObject | None = None

    @model_serializer(mode="wrap")
    def _only_set_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        # An unset field must not reach a reader as ``null``: to a reader that
        # would be an instruction to clear it.
        dumped = handler(self)
        return {key: value for key, value in dumped.items() if key in self.model_fields_set}


class ThreadMetaUpdated(_Body):
    """A change to the mirrored thread row.

    Emitted by ``agent.transcript.mirror`` whenever a LangGraph metadata write
    changes a key the transcript read path authorizes against, or the title the
    snapshot serves. ``patch.metadata`` merges key by key.
    """

    type: Literal["thread.meta_updated"] = "thread.meta_updated"
    patch: ThreadMetaPatch


class TurnRequested(_Body):
    type: Literal["turn.requested"] = "turn.requested"
    turn_id: UUID
    message_id: str
    text: str
    sender: MessageSender
    attachments: list[MessageAttachment] = Field(default_factory=list)
    model_id: str | None = None
    effort: str | None = None
    plan_mode: bool = False


class TurnStarted(_Body):
    type: Literal["turn.started"] = "turn.started"
    turn_id: UUID
    run_id: str


class TurnQueued(_Body):
    """A requested turn now has a run waiting behind the live one.

    The run starts on its own when the thread goes idle; until then the turn
    stays ``requested`` and the run id is what a cancel needs.
    """

    type: Literal["turn.queued"] = "turn.queued"
    turn_id: UUID
    run_id: str


class TurnCompleted(_Body):
    type: Literal["turn.completed"] = "turn.completed"
    turn_id: UUID
    run_id: str | None = None


class CheckpointFile(BaseModel):
    """One file a turn changed, as ``git diff --numstat`` counted it."""

    model_config = ConfigDict(extra="forbid")

    path: str
    additions: int
    deletions: int
    status: FileChangeKind


class TurnCheckpointCompleted(_Body):
    """The commit that records what the sandbox's working tree held at turn end.

    No writer emits this at present: capturing it ran a git script in the
    sandbox at the end of every turn, and the run's completion waited on it.
    The event stays so what was recorded still projects and the wire contract
    holds for when capture returns, off the run's critical path.

    ``commit`` is a parentless commit object no branch points at, written
    without touching HEAD, the index or the working tree, and named by
    ``checkpoint_ref``. The sandbox is ephemeral, so
    the sha is what outlives it: the ref only resolves while the sandbox is
    alive, but the sha still identifies the tree if the ref is ever pushed or
    compared against a pull request. ``checkpoint_turn_count`` is the 1-based
    ordinal ``append`` assigns under the thread's lock; a writer leaves it at
    ``0``, and the ref is named by turn id so it does not depend on it.
    ``files`` is the diff against the previous turn's checkpoint, or against
    the head the turn started from.

    ``status`` is ``missing`` when there was nothing to checkpoint (no sandbox,
    or no repository in it) and ``error`` when the capture itself failed, with
    the reason in ``error``.
    """

    type: Literal["turn.checkpoint.completed"] = "turn.checkpoint.completed"
    turn_id: UUID
    checkpoint_turn_count: int = 0
    checkpoint_ref: str
    commit: str | None = None
    status: CheckpointStatus
    files: list[CheckpointFile] = Field(default_factory=list)
    assistant_message_id: str | None = None
    error: str | None = None


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
    attachments: list[MessageAttachment] | None = None
    usage: MessageUsage | None = None
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
    """A finished tool call, carrying a preview of its output rather than all of it.

    The full output is written to ``thread_tool_output`` out of band — it rides
    on the command, not in this payload, the way attachment bytes do — and is
    served on demand by the tool-output endpoint. ``output_truncated`` says the
    stored output was itself cut at the size cap, so even that endpoint has no
    more.
    """

    type: Literal["tool.completed"] = "tool.completed"
    turn_id: UUID
    tool_call_id: str
    status: ToolOutcome
    output_preview: str | None = None
    output_truncated: bool = False
    has_output: bool = False
    namespace: list[str] = Field(default_factory=list)


class RunNotice(_Body):
    """A hint about how the run is being executed.

    Notices have no projection: the snapshot serves the latest one per kind for
    the thread's newest turn, read straight from the log, so ``model_routed``
    survives a reload. ``conversation_offloading`` describes what a run is doing
    right now, so the snapshot drops it once its turn has settled.
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
    | TurnQueued
    | TurnCompleted
    | TurnCheckpointCompleted
    | TurnFailed
    | TurnInterrupted
    | MessageAppended
    | MessageCompleted
    | ToolStarted
    | ToolCompleted
    | RunNotice,
    Field(discriminator="type"),
]


TRANSCRIPT_EVENT_ADAPTER: TypeAdapter[TranscriptEvent] = TypeAdapter(TranscriptEvent)
"""Validates a stored ``thread_event.payload`` back into the body that wrote it."""


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
