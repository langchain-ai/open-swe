"""Folding a legacy thread's LangGraph history into a transcript.

A thread that predates the event log — or an imported session, whose history
is written straight into graph state — is served by hydrating its whole
LangGraph state at once. :func:`upgrade_transcript` replays that state through
the event taxonomy a run would have written, and only then stamps the thread
``transcript: v2`` so the dashboard switches readers.

It is safe to run twice and alongside runs:

* The log is written in one transaction under the thread's append lock, so a
  reader never sees half of it and a concurrent upgrade waits, then finds the
  work done.
* The ``thread.created`` receipt names the checkpoint the log was folded from.
  A retry after a failed stamp reuses that log; a log left behind by anything
  else — an older checkpoint, a run whose own stamp never landed — is dropped
  and folded again from the state, which holds everything it did.
* A thread with a live run is refused. A run that starts mid-upgrade waits on
  the same lock in the transcript middleware, so it either makes the upgrade
  refuse at commit or is recorded on top of the committed log.
"""

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import batched

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    messages_from_dict,
)
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openswe.database import postgres
from openswe.input_messages import message_sender_id
from openswe.threads.summary import (
    SESSION_UPLOAD_PENDING_KEY,
    TRANSCRIPT_VERSION,
)
from openswe.transcript.attachments import PendingAttachment
from openswe.transcript.engine import (
    AppendResult,
    Command,
    announce,
    append_in,
    delete_in,
    lock_thread,
)
from openswe.transcript.events import (
    TOOL_OUTPUT_PREVIEW_CHARS,
    ActorKind,
    JsonObject,
    MessageCompleted,
    MessageSender,
    ThreadMetaPatch,
    ThreadMetaUpdated,
    ToolCompleted,
    ToolStarted,
    TranscriptEvent,
    TurnCompleted,
    TurnRequested,
)
from openswe.transcript.messages import (
    capped_tool_output,
    human_attachments,
    is_dynamic_context,
    json_object,
    message_text,
    message_usage,
    nonempty_string,
    reasoning_text,
    thread_created,
    transcribed_human_text,
)
from openswe.transcript.mirror import MIRRORED_KEYS
from openswe.utils.json_types import as_json_object, thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

UPGRADE_FLAG = "experimental_transcript_upgrade"
"""The personal experiment under which legacy threads are upgraded, on open or on import."""

_BATCH = 200
"""Commands appended per progress report."""


class ThreadBusy(Exception):
    """The thread has a live run, or is still waiting for its imported history."""

    def __init__(self, thread_id: str) -> None:
        super().__init__(f"thread {thread_id} is busy")
        self.thread_id = thread_id


class UpgradeProgress(BaseModel):
    done: int
    total: int


type ProgressCallback = Callable[[UpgradeProgress], None]


def is_transcribed(metadata: Mapping[str, object]) -> bool:
    return metadata.get("transcript") == TRANSCRIPT_VERSION


def _require_idle(thread_id: str, thread: Mapping[str, object]) -> None:
    metadata = thread_metadata(thread)
    if (
        thread.get("status") == "busy"
        or metadata.get("latest_run_status") in {"pending", "running"}
        or metadata.get(SESSION_UPLOAD_PENDING_KEY) is True
    ):
        raise ThreadBusy(thread_id)


def _messages(values: object) -> list[BaseMessage]:
    """The ``messages`` channel of a serialized graph state, as LangChain messages."""
    raw = as_json_object(values).get("messages")
    messages: list[BaseMessage] = []
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            continue
        try:
            messages.extend(messages_from_dict([{"type": item["type"], "data": item}]))
        except ValueError, TypeError:
            logger.warning(
                "Skipping an unreadable message while backfilling a transcript",
                exc_info=True,
                extra={"transcript": {"message_type": item["type"]}},
            )
    return messages


def _created_at(thread: Mapping[str, object]) -> datetime:
    raw = thread.get("created_at")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            logger.warning("Unparseable thread created_at", extra={"created_at": raw})
    return datetime.now(UTC)


@dataclass(frozen=True)
class LegacyHistory:
    """A thread's LangGraph state at one checkpoint, and the commands it folds into."""

    thread_id: str
    checkpoint_id: str
    metadata: JsonObject
    messages: Sequence[BaseMessage]
    started_at: datetime

    @classmethod
    def from_state(cls, thread: Mapping[str, object], state: object) -> LegacyHistory:
        thread_id = str(thread.get("thread_id"))
        checkpoint = as_json_object(as_json_object(state).get("checkpoint"))
        return cls(
            thread_id=thread_id,
            checkpoint_id=nonempty_string(checkpoint.get("checkpoint_id")) or "empty",
            metadata=thread_metadata(thread),
            messages=_messages(as_json_object(state).get("values")),
            started_at=_created_at(thread),
        )

    @property
    def created_command_id(self) -> str:
        """The receipt that says this exact checkpoint was folded into the log."""
        return f"thread:{self.thread_id}:backfilled:{self.checkpoint_id}"

    def commands(self) -> list[Command]:
        folder = _TurnFolder(self)
        for message in self.messages:
            folder.fold(message)
        return folder.finish()


@dataclass
class _TurnFolder:
    """Groups a flat message history into the turns a run would have recorded.

    Each human request opens a turn that the next one closes. Timestamps are
    synthetic but strictly increasing, from the thread's creation: the read
    path orders turns, messages and tool calls by them, and the state keeps no
    real ones.
    """

    history: LegacyHistory
    commands: list[Command] = field(default_factory=list)
    turn_id: uuid.UUID | None = None
    started_tools: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.commands.append(
            Command(
                command_id=self.history.created_command_id,
                event=thread_created(self.history.metadata, ""),
                actor_kind="system",
                occurred_at=self.history.started_at,
            )
        )

    def fold(self, message: BaseMessage) -> None:
        if isinstance(message, HumanMessage):
            self._human(message)
        elif isinstance(message, AIMessage):
            self._ai(message)
        elif isinstance(message, ToolMessage):
            self._tool_result(message)

    def finish(self) -> list[Command]:
        self._close_turn()
        return self.commands

    def _clock(self) -> datetime:
        return self.history.started_at + timedelta(microseconds=len(self.commands))

    def _add(
        self,
        command_id: str,
        event: TranscriptEvent,
        *,
        actor_kind: ActorKind,
        attachments: tuple[PendingAttachment, ...] = (),
        tool_output: str | None = None,
    ) -> None:
        self.commands.append(
            Command(
                command_id=command_id,
                event=event,
                actor_kind=actor_kind,
                occurred_at=self._clock(),
                turn_id=self.turn_id,
                attachments=attachments,
                tool_output=tool_output,
            )
        )

    def _open_turn(self) -> uuid.UUID:
        if self.turn_id is None:
            self.turn_id = uuid.uuid7()
        return self.turn_id

    def _close_turn(self) -> None:
        if self.turn_id is None:
            return
        self._add(
            f"turn:{self.turn_id}:completed",
            TurnCompleted(turn_id=self.turn_id),
            actor_kind="system",
        )
        self.turn_id = None

    def _human(self, message: HumanMessage) -> None:
        if message.additional_kwargs.get("lc_source") == "summarization":
            return
        message_id = message.id or str(uuid.uuid7())
        text = transcribed_human_text(message)
        attachments, pending = human_attachments(message, message_id)
        if not text and not attachments:
            return
        if is_dynamic_context(message):
            turn_id = self._open_turn()
            self._add(
                f"human:{message_id}",
                MessageCompleted(
                    turn_id=turn_id,
                    message_id=message_id,
                    role="human",
                    text=text,
                    attachments=attachments or None,
                    created_at=self._clock(),
                ),
                actor_kind="user",
                attachments=pending,
            )
            return
        self._close_turn()
        turn_id = self._open_turn()
        metadata = self.history.metadata
        self._add(
            f"turn:{turn_id}:requested",
            TurnRequested(
                turn_id=turn_id,
                message_id=message_id,
                text=text,
                sender=MessageSender(
                    login=message_sender_id(message.content, kind="human")
                    or nonempty_string(metadata.get("owner_login"))
                    or "unknown",
                    kind=nonempty_string(metadata.get("source")) or "unknown",
                ),
                attachments=attachments,
            ),
            actor_kind="user",
            attachments=pending,
        )

    def _ai(self, message: AIMessage) -> None:
        turn_id = self._open_turn()
        message_id = message.id or str(uuid.uuid7())
        text = message_text(message).strip()
        reasoning = reasoning_text(message).strip()
        if text or reasoning:
            self._add(
                f"message:{message_id}:completed",
                MessageCompleted(
                    turn_id=turn_id,
                    message_id=message_id,
                    role="ai",
                    text=text,
                    reasoning=reasoning,
                    usage=message_usage(message),
                    created_at=self._clock(),
                ),
                actor_kind="agent",
            )
        for call in message.tool_calls:
            tool_call_id = call.get("id")
            if not tool_call_id or tool_call_id in self.started_tools:
                continue
            self.started_tools.add(tool_call_id)
            self._add(
                f"tool:{tool_call_id}:started",
                ToolStarted(
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    message_id=message_id,
                    name=call.get("name") or "tool",
                    input=json_object(call.get("args")),
                ),
                actor_kind="agent",
            )

    def _tool_result(self, message: ToolMessage) -> None:
        if message.tool_call_id not in self.started_tools or self.turn_id is None:
            return
        output, truncated = capped_tool_output(message.content)
        self._add(
            f"tool:{message.tool_call_id}:completed",
            ToolCompleted(
                turn_id=self.turn_id,
                tool_call_id=message.tool_call_id,
                status="error" if message.status == "error" else "completed",
                output_preview=output[:TOOL_OUTPUT_PREVIEW_CHARS] or None,
                output_truncated=truncated,
                has_output=bool(output),
            ),
            actor_kind="agent",
            tool_output=output,
        )


async def _folded(conn: AsyncConnection, history: LegacyHistory) -> bool:
    """Whether the log already holds exactly this checkpoint's history."""
    result = await conn.execute(
        text(
            """
            SELECT 1 FROM thread_command_receipt
            WHERE thread_id = :thread_id AND command_id = :command_id
              AND result_version IS NOT NULL
            """
        ),
        {"thread_id": history.thread_id, "command_id": history.created_command_id},
    )
    return result.scalar_one_or_none() is not None


def _mirror_command(metadata: Mapping[str, object]) -> Command:
    """Re-read authorization metadata as of commit, so a flip mid-upgrade is not lost."""
    mirrored = json_object({key: metadata[key] for key in MIRRORED_KEYS if key in metadata})
    mirrored["transcript"] = TRANSCRIPT_VERSION
    title = mirrored.get("title")
    return Command(
        command_id=str(uuid.uuid7()),
        event=ThreadMetaUpdated(
            patch=ThreadMetaPatch(
                title=title if isinstance(title, str) else None, metadata=mirrored
            )
        ),
        actor_kind="system",
    )


async def upgrade_transcript(thread_id: str, on_progress: ProgressCallback | None = None) -> None:
    """Fold ``thread_id``'s LangGraph history into a transcript, then stamp it.

    A no-op for a thread that is already stamped. Raises :class:`ThreadBusy`
    when a run is live or the thread is still waiting for an imported session.
    """
    postgres.require_configured()
    client = langgraph_client()
    report = on_progress or (lambda _: None)
    if is_transcribed(thread_metadata(await client.threads.get(thread_id))):
        return
    results: list[AppendResult] = []
    async with postgres.transaction() as conn:
        await lock_thread(conn, thread_id)
        thread = await client.threads.get(thread_id)
        if is_transcribed(thread_metadata(thread)):
            return
        _require_idle(thread_id, thread)
        history = LegacyHistory.from_state(thread, await client.threads.get_state(thread_id))
        if not await _folded(conn, history):
            await delete_in(conn, thread_id)
            commands = history.commands()
            done = 0
            report(UpgradeProgress(done=0, total=len(commands)))
            for batch in batched(commands, _BATCH, strict=False):
                results.append(await append_in(conn, thread_id, batch))
                done += len(batch)
                report(UpgradeProgress(done=done, total=len(commands)))
        current = await client.threads.get(thread_id)
        _require_idle(thread_id, current)
        results.append(
            await append_in(conn, thread_id, [_mirror_command(thread_metadata(current))])
        )
    for result in results:
        announce(thread_id, result)
    # Only after the log commits: the dashboard switches readers on the stamp.
    # Its mirror rode in the transaction, with the authorization keys as of commit.
    await client.threads.update(thread_id, metadata={"transcript": TRANSCRIPT_VERSION})
    logger.info(
        "Upgraded a thread to the transcript",
        extra={
            "transcript": {
                "thread_id": thread_id,
                "checkpoint_id": history.checkpoint_id,
                "messages": len(history.messages),
            }
        },
    )
