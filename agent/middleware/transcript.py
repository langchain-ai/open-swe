"""Write agent activity into the append-only transcript event log.

One middleware instance serves every run of a graph, so per-run state lives in a
module-level registry keyed by ``thread_id:run_id``. A ``ContextVar`` cannot
hold it: LangGraph runs each node in its own copied context, so a value set in
``abefore_agent`` is invisible to the model node. The subagent *namespace* is
the exception — it only travels downward into a nested graph invoked from
inside the tool coroutine, which a ``ContextVar`` does correctly.

Transcript writes are observability: every failure is logged and swallowed so a
transcript problem can never fail a run. Events are queued and written by one
background writer per run, so the model stream never waits on Postgres.
"""

import asyncio
import base64
import binascii
import contextlib
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.callbacks.manager import AsyncCallbackManager, CallbackManager
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command as GraphCommand
from langgraph_sdk import get_client
from pydantic import JsonValue
from pydantic_core import PydanticSerializationError, to_jsonable_python
from sqlalchemy import text as sql

from agent.database import postgres
from agent.input_messages import (
    input_message_text,
    message_sender_id,
)
from agent.middleware.trace import OpenSWEMiddleware
from agent.threads.changes import publish_thread_changed
from agent.transcript.attachments import PendingAttachment, UnsupportedAttachment
from agent.transcript.engine import Command, append
from agent.transcript.events import (
    TOOL_OUTPUT_PREVIEW_CHARS,
    JsonObject,
    MessageAppended,
    MessageAttachment,
    MessageCompleted,
    MessageSender,
    MessageUsage,
    RunNotice,
    ThreadCreated,
    ToolCompleted,
    ToolStarted,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnRequested,
    TurnStarted,
)

logger = logging.getLogger(__name__)

PARAGRAPH_FLUSH_SECONDS = 0.4
HARD_FLUSH_CHARS = 24_000
TOOL_OUTPUT_CAP_BYTES = 256 * 1024
ERROR_TEXT_CAP = 2_000
_WRITER_BATCH = 32
# Model calls a middleware makes for its own bookkeeping (routing classifier,
# conversation offloading) are tagged out of the user-facing stream. Their
# tokens must not become transcript fragments.
_HIDDEN_TAGS = frozenset({"nostream", "langsmith:hidden"})
_RUN_SETTLE_POLL_SECONDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0)
_RUN_LIVE_STATUSES = frozenset({"pending", "running"})
_run_end_announcers: set[asyncio.Task[None]] = set()

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])\s")

# Namespace of the enclosing ``task`` tool calls; travels into nested subagents.
_namespace: ContextVar[tuple[str, ...]] = ContextVar("transcript_namespace", default=())


def current_namespace() -> list[str]:
    """The subagent namespace for events emitted right now (``[]`` at the root)."""
    return list(_namespace.get())


def paragraph_boundary(text: str) -> int:
    """The largest safe markdown flush offset in ``text`` (0 when there is none).

    Safe boundaries are the end of a blank line, the end of a closing code
    fence, and the start of a list item; offsets inside an open fence never
    qualify. The trailing partial line is never flushed, but a boundary in
    front of it is one once it has declared itself a list item.
    """
    best = 0
    offset = 0
    in_fence = False
    fence = ""
    lines = text.split("\n")
    for line in lines[:-1]:
        length = len(line) + 1
        match = _FENCE.match(line)
        if in_fence:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                in_fence = False
                fence = ""
                best = offset + length
        elif match:
            in_fence = True
            fence = match.group(1)
        elif not line.strip():
            best = offset + length
        elif offset and _LIST_ITEM.match(line):
            best = offset
        offset += length
    if offset and not in_fence and _LIST_ITEM.match(lines[-1]):
        best = offset
    return best


@dataclass
class ParagraphBuffer:
    """Paragraph-mode delta batching for one stream of one message."""

    last_flush: float
    pending: str = ""

    def add(self, fragment: str) -> None:
        self.pending += fragment

    def take(self, now: float, *, final: bool = False) -> str | None:
        """The next fragment to emit, or ``None`` while it should keep buffering."""
        if not self.pending:
            return None
        if final:
            return self._cut(len(self.pending), now)
        if len(self.pending) >= HARD_FLUSH_CHARS:
            return self._cut(paragraph_boundary(self.pending) or len(self.pending), now)
        if now - self.last_flush < PARAGRAPH_FLUSH_SECONDS:
            return None
        boundary = paragraph_boundary(self.pending)
        if not boundary:
            return None
        return self._cut(boundary, now)

    def _cut(self, index: int, now: float) -> str:
        fragment, self.pending = self.pending[:index], self.pending[index:]
        self.last_flush = now
        return fragment


@dataclass
class MessageBuffers:
    text: ParagraphBuffer
    reasoning: ParagraphBuffer


@dataclass
class RunState:
    """Everything one transcribed run needs, shared across its node tasks."""

    thread_id: str
    run_id: str
    turn_id: UUID
    enabled: bool
    seen_human_ids: set[str] = field(default_factory=set)
    buffers: dict[str, MessageBuffers] = field(default_factory=dict)
    message_alias: dict[str, str] = field(default_factory=dict)
    offloading_notice: JsonObject | None = None
    queue: asyncio.Queue[Command] | None = None
    writer: asyncio.Task[None] | None = None
    terminal: bool = False

    def enqueue(self, *commands: Command) -> None:
        """Queue commands for the writer. Nothing is shed: the turn end drains."""
        if not self.enabled or self.queue is None:
            return
        for command in commands:
            self.queue.put_nowait(command)


_runs: dict[str, RunState] = {}

DISABLED = RunState(thread_id="", run_id="", turn_id=uuid.uuid7(), enabled=False)


def _run_key(thread_id: str, run_id: str) -> str:
    return f"{thread_id}:{run_id}"


@dataclass(frozen=True)
class RunIds:
    """The correlation ids for the run executing right now."""

    thread_id: str
    run_id: str
    turn_id: UUID | None
    configurable: Mapping[str, object]


def _run_ids() -> RunIds | None:
    """Correlation ids from the running graph's config, or ``None`` off-graph.

    A direct ``graph.ainvoke`` (evals, the desktop app) has no ``run_id``; a
    minted one would differ per lookup, so such a run is not recorded at all.
    """
    try:
        config = get_config()
    except RuntimeError:
        return None
    raw = config.get("configurable")
    configurable: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return None
    run_id = config.get("run_id") or configurable.get("run_id")
    if not isinstance(run_id, (str, UUID)) or not run_id:
        return None
    turn_id = configurable.get("transcript_turn_id")
    return RunIds(
        thread_id=thread_id,
        run_id=str(run_id),
        turn_id=_parse_turn_id(turn_id if isinstance(turn_id, str) else None),
        configurable=configurable,
    )


def _lookup_state() -> RunState:
    """The state for the current run, or ``DISABLED`` when it is not transcribed."""
    ids = _run_ids()
    if ids is None:
        return DISABLED
    return _runs.get(_run_key(ids.thread_id, ids.run_id), DISABLED)


def _reasoning_text(message: BaseMessage) -> str:
    """Concatenated reasoning. Never stripped: a streaming chunk's leading space
    is part of the delta."""
    try:
        blocks = message.content_blocks
    except Exception:
        logger.debug("Could not read content blocks for reasoning", exc_info=True)
        return ""
    out = ""
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "reasoning":
            reasoning = block.get("reasoning")
            if isinstance(reasoning, str):
                out += reasoning
        elif block.get("type") == "non_standard":
            value = block.get("value")
            if isinstance(value, Mapping) and value.get("type") == "thinking":
                thinking = value.get("thinking")
                if isinstance(thinking, str):
                    out += thinking
    return out


def _message_text(message: BaseMessage) -> str:
    try:
        return message.text
    except Exception:
        logger.debug("Could not read message text", exc_info=True)
        return ""


def _human_text(message: HumanMessage) -> str:
    """The user's own words, unwrapping the ``<input-message>`` envelope."""
    authored = input_message_text(message.content)
    return (authored or _message_text(message)).strip()


def _transcribed_human_text(message: HumanMessage) -> str:
    """A human message as the reader has to receive it: envelope and all.

    Who sent it, on which surface, and whether it is a platform-generated
    context message the UI hides are all carried by the ``<input-message>``
    wrapper and by nothing else on the wire. Unwrapping here would strip a
    message of its attribution.
    """
    return _message_text(message).strip()


def _is_dynamic_context(message: HumanMessage) -> bool:
    """A ``<dynamic-context>`` introduction: who a sender is, not what they said."""
    return "<dynamic-context" in _message_text(message)


def _is_turn_annotation(message: HumanMessage) -> bool:
    """A platform block that annotates the turn instead of being it.

    A ``<dynamic-context>`` introduction can trail the message it describes, so
    the last human message in state is not reliably the request itself.
    """
    return _is_dynamic_context(message)


def _usage(message: AIMessage) -> MessageUsage | None:
    """Token accounting for one AI message, when the provider reported any."""
    usage = message.usage_metadata
    if not isinstance(usage, Mapping):
        return None
    counts = {
        key: value
        for key in ("input_tokens", "output_tokens", "total_tokens")
        if isinstance(value := usage.get(key), int)
    }
    return MessageUsage(**counts) if counts else None


def _image_bytes(block: Mapping[str, object]) -> tuple[str, bytes] | None:
    """``(mime_type, data)`` for a standard base64 image content block."""
    mime_type = block.get("mime_type")
    encoded = block.get("base64")
    if not isinstance(mime_type, str) or not isinstance(encoded, str):
        return None
    try:
        return mime_type, base64.b64decode(encoded, validate=True)
    except binascii.Error:
        logger.warning("Skipping an undecodable transcript attachment", exc_info=True)
        return None


def _human_attachments(
    message: HumanMessage, message_id: str
) -> tuple[list[MessageAttachment], tuple[PendingAttachment, ...]]:
    """Files on a human message, as event metadata plus the bytes to store.

    Only standard base64 image blocks are captured; a remote-URL image is
    referenced rather than copied, and anything else is skipped with a log.
    """
    try:
        blocks = message.content_blocks
    except Exception:
        logger.debug("Could not read content blocks for attachments", exc_info=True)
        return [], ()
    attachments: list[MessageAttachment] = []
    pending: list[PendingAttachment] = []
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("type") != "image":
            continue
        url = block.get("url")
        decoded = _image_bytes(block)
        if decoded is None:
            if isinstance(url, str) and url:
                attachments.append(MessageAttachment(mime_type="image/*", url=url))
            else:
                logger.warning(
                    "Skipping a transcript attachment with no bytes and no url",
                    extra={"transcript": {"message_id": message_id}},
                )
            continue
        mime_type, data = decoded
        file_name = block.get("file_name")
        attachment_id = uuid.uuid7()
        try:
            pending.append(
                PendingAttachment(
                    attachment_id=attachment_id,
                    message_id=message_id,
                    position=len(attachments),
                    mime_type=mime_type,
                    file_name=file_name if isinstance(file_name, str) else None,
                    data=data,
                )
            )
        except UnsupportedAttachment:
            logger.warning(
                "Skipping an unsupported transcript attachment",
                exc_info=True,
                extra={"transcript": {"message_id": message_id, "mime_type": mime_type}},
            )
            continue
        attachments.append(
            MessageAttachment(
                mime_type=mime_type,
                file_name=file_name if isinstance(file_name, str) else None,
                attachment_id=attachment_id,
            )
        )
    return attachments, tuple(pending)


def _tool_output(content: object) -> tuple[str, bool]:
    if isinstance(content, str):
        text = content
    elif isinstance(content, (list, dict)):
        parts: list[str] = []
        for block in content if isinstance(content, list) else [content]:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(cast(str, block["text"]))
            else:
                parts.append(str(block))
        text = "\n".join(parts)
    else:
        text = str(content)
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= TOOL_OUTPUT_CAP_BYTES:
        return text, False
    return encoded[:TOOL_OUTPUT_CAP_BYTES].decode("utf-8", "ignore"), True


class _DeltaHandler(AsyncCallbackHandler):
    """Feeds the streaming deltas of one model call into one message's buffers.

    Chunk ids are deliberately ignored: a provider need not name its message
    the same way in every chunk (the OpenAI Responses stream alternates between
    the real ``resp_...`` id and LangChain's ``lc_run--<run_id>`` placeholder),
    so keying the buffers on the chunk would split one reply across several
    transcript messages. One id is minted per model call instead, and
    ``message.completed`` reuses it.
    """

    run_inline = True

    def __init__(self, state: RunState, namespace: list[str], message_id: str) -> None:
        self._state = state
        self._namespace = namespace
        self.message_id = message_id
        self._protocol: Literal["v1", "v2"] | None = None
        self.streamed = False

    async def on_llm_new_token(
        self,
        token: object,
        *,
        chunk: object = None,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: object,
    ) -> None:
        if self._skip("v1", tags):
            return
        message = getattr(chunk, "message", chunk)
        if not isinstance(message, BaseMessage):
            self._feed(token if isinstance(token, str) else "", "")
            return
        text = _message_text(message) or (token if isinstance(token, str) else "")
        self._feed(text, _reasoning_text(message))

    async def on_stream_event(
        self,
        event: Mapping[str, object],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: object,
    ) -> None:
        if self._skip("v2", tags) or event.get("event") != "content-block-delta":
            return
        delta = event.get("delta")
        if not isinstance(delta, Mapping):
            return
        text = delta.get("text")
        reasoning = delta.get("reasoning")
        self._feed(
            text if isinstance(text, str) else "",
            reasoning if isinstance(reasoning, str) else "",
        )

    def _skip(self, protocol: Literal["v1", "v2"], tags: list[str] | None) -> bool:
        """Drop hidden model calls, and whichever streaming protocol did not win."""
        if _HIDDEN_TAGS & set(tags or ()):
            return True
        if self._protocol is None:
            self._protocol = protocol
        return self._protocol != protocol

    def _feed(self, text: str, reasoning: str) -> None:
        if not text and not reasoning:
            return
        self.streamed = True
        try:
            buffers = _buffers(self._state, self.message_id)
            if text:
                buffers.text.add(text)
            if reasoning:
                buffers.reasoning.add(reasoning)
            _flush_message(self._state, self.message_id, self._namespace)
        except Exception:
            logger.warning(
                "Transcript delta capture failed",
                exc_info=True,
                extra={"transcript_message_id": self.message_id},
            )


def _buffers(state: RunState, message_id: str) -> MessageBuffers:
    buffers = state.buffers.get(message_id)
    if buffers is None:
        now = time.monotonic()
        buffers = MessageBuffers(
            text=ParagraphBuffer(last_flush=now), reasoning=ParagraphBuffer(last_flush=now)
        )
        state.buffers[message_id] = buffers
    return buffers


def _flush_message(
    state: RunState, message_id: str, namespace: list[str], *, final: bool = False
) -> None:
    buffers = state.buffers.get(message_id)
    if buffers is None:
        return
    now = time.monotonic()
    text = buffers.text.take(now, final=final)
    reasoning = buffers.reasoning.take(now, final=final)
    if text is None and reasoning is None:
        return
    event = MessageAppended(
        type="message.appended",
        turn_id=state.turn_id,
        message_id=message_id,
        namespace=list(namespace),
        text=text,
        reasoning=reasoning,
    )
    state.enqueue(
        Command(
            command_id=str(uuid.uuid7()),
            event=event,
            actor_kind="agent",
            run_id=state.run_id,
            turn_id=state.turn_id,
        )
    )


async def _writer_loop(state: RunState) -> None:
    """The single writer for a run: batches queued commands into ``append`` calls."""
    queue = state.queue
    if queue is None:
        return
    while True:
        command = await queue.get()
        batch = [command]
        try:
            while len(batch) < _WRITER_BATCH:
                batch.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            pass
        try:
            await append(state.thread_id, batch)
        except Exception:
            logger.warning(
                "Transcript append failed",
                exc_info=True,
                extra={
                    "transcript_thread_id": state.thread_id,
                    "transcript_batch": len(batch),
                },
            )
        finally:
            for _ in batch:
                queue.task_done()


async def _finish(state: RunState) -> None:
    """Flush the queue, then stop the writer — even if the drain is cancelled,
    which is the process shutting down and must not leave the writer behind."""
    try:
        if state.queue is not None:
            await asyncio.wait_for(state.queue.join(), timeout=30)
    except TimeoutError:
        logger.warning(
            "Transcript drain timed out",
            exc_info=True,
            extra={"transcript_thread_id": state.thread_id},
        )
    finally:
        if state.writer is not None:
            state.writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.writer
            state.writer = None
        _runs.pop(_run_key(state.thread_id, state.run_id), None)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _thread_created(metadata: Mapping[str, object], title: str) -> ThreadCreated:
    return ThreadCreated(
        type="thread.created",
        title=_string(metadata.get("title")) or title or "New agent",
        source=_string(metadata.get("source")) or _string(metadata.get("origin")) or "unknown",
        owner_login=_string(metadata.get("owner_login")) or "",
        visibility="private" if metadata.get("visibility") == "private" else "public",
        repo_owner=_string(metadata.get("repo_owner")),
        repo_name=_string(metadata.get("repo_name")),
        model_id=_string(metadata.get("resolved_model")) or _string(metadata.get("model")),
        effort=_string(metadata.get("resolved_effort")) or _string(metadata.get("effort")),
        metadata=_json_object(metadata),
    )


async def _has_transcript(thread_id: str) -> bool:
    """Whether the thread is served by the event log rather than by LangGraph state."""
    if not postgres.configured():
        return False
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            sql("SELECT 1 FROM thread WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        return result.scalar_one_or_none() is not None


async def _thread_metadata(thread_id: str) -> dict[str, object]:
    """The LangGraph thread metadata a ``thread.created`` event is built from."""
    thread = await get_client().threads.get(thread_id)
    metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
    return dict(metadata) if isinstance(metadata, Mapping) else {}


async def _stamp_transcript(thread_id: str) -> None:
    """Point the UI at the transcript reader. Only ever after the row exists."""
    await get_client().threads.update(thread_id, metadata={"transcript": "v2"})


def _announce_run_end(thread_id: str, run_id: str) -> None:
    """Tell live sidebars about the thread once LangGraph reports this run over.

    The turn settles here before the platform records the run's outcome, so a
    sidebar that re-read the thread straight away would still see it running.
    This is the end-of-run signal for deployments without the completion
    webhook, and it runs whether or not the thread keeps a transcript.
    """
    if not thread_id or not run_id:
        return
    task = asyncio.create_task(_publish_when_run_settles(thread_id, run_id))
    _run_end_announcers.add(task)
    task.add_done_callback(_run_end_announcers.discard)


async def _publish_when_run_settles(thread_id: str, run_id: str) -> None:
    for delay in _RUN_SETTLE_POLL_SECONDS:
        await asyncio.sleep(delay)
        try:
            run = await get_client().runs.get(thread_id, run_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not read the status of a settling run",
                exc_info=True,
                extra={"thread_id": thread_id, "run_id": run_id},
            )
            break
        if run.get("status") not in _RUN_LIVE_STATUSES:
            break
    await publish_thread_changed(thread_id)


class TranscriptMiddleware(OpenSWEMiddleware):
    """Append the run's activity to the thread's transcript event log."""

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        del runtime
        try:
            await self._start_run(state)
        except Exception:
            logger.warning("Transcript run start failed", exc_info=True)
        return None

    async def _start_run(self, state: AgentState) -> None:
        ids = _run_ids()
        if ids is None or _namespace.get():
            return
        key = _run_key(ids.thread_id, ids.run_id)
        if key in _runs:
            return

        messages = cast(Sequence[BaseMessage], state.get("messages") or [])
        transcribed = await _has_transcript(ids.thread_id)
        # Without PostgreSQL there is nowhere to keep a transcript, so the run
        # must not be stamped as one: the UI would switch to a read path that
        # has no rows behind it.
        untranscribable = not postgres.configured() or (
            not transcribed and any(isinstance(message, AIMessage) for message in messages)
        )
        if untranscribable:
            # A thread that already has agent turns but no transcript row predates
            # the event log. Stay out of it for the whole run.
            _runs[key] = RunState(
                thread_id=ids.thread_id, run_id=ids.run_id, turn_id=uuid.uuid7(), enabled=False
            )
            return

        human = next(
            (
                m
                for m in reversed(messages)
                if isinstance(m, HumanMessage) and not _is_turn_annotation(m)
            ),
            None,
        )
        metadata: Mapping[str, object] = {}
        if not transcribed:
            metadata = await _thread_metadata(ids.thread_id)
            title = _human_text(human) if human is not None else ""
            # Committed before the stamp, and before the run is enabled: the UI
            # switches to the transcript reader on the stamp alone, so a failure
            # here has to leave the thread reading LangGraph state.
            await append(
                ids.thread_id,
                [
                    Command(
                        command_id=f"thread:{ids.thread_id}:created",
                        event=_thread_created(metadata, title[:80]),
                        actor_kind="system",
                        run_id=ids.run_id,
                    )
                ],
            )
            await _stamp_transcript(ids.thread_id)

        turn_id = ids.turn_id or uuid.uuid7()
        run_state = RunState(
            thread_id=ids.thread_id, run_id=ids.run_id, turn_id=turn_id, enabled=True
        )
        run_state.queue = asyncio.Queue()
        run_state.writer = asyncio.create_task(_writer_loop(run_state))
        _runs[key] = run_state

        commands: list[Command] = []
        if (not transcribed or ids.turn_id is None) and human is not None:
            commands.append(_turn_requested(run_state, human, ids, metadata))
        commands.append(
            Command(
                command_id=f"turn:{turn_id}:started:{ids.run_id}",
                event=TurnStarted(type="turn.started", turn_id=turn_id, run_id=ids.run_id),
                actor_kind="agent",
                run_id=ids.run_id,
                turn_id=turn_id,
            )
        )
        # Every human message already in state belongs to a turn that is over.
        # Only a message injected after the run started is new, and recording
        # an older one again would move it into this turn. The exception is a
        # ``<dynamic-context>`` introduction: it names a sender the reader has
        # to resolve, it is injected once per thread rather than per turn, and
        # it renders as nothing, so recording it is what makes attribution work
        # and moving it costs nothing.
        run_state.seen_human_ids.update(
            message.id
            for message in messages
            if isinstance(message, HumanMessage)
            and isinstance(message.id, str)
            and message.id
            and not _is_dynamic_context(message)
        )
        run_state.enqueue(*commands)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        state = _lookup_state()
        if not state.enabled:
            return await handler(request)
        namespace = current_namespace()
        try:
            if not namespace:
                # A subagent's own first message is the task prompt, not
                # something a person said, and its offloading is the parent's.
                self._record_injected_humans(state, request)
                self._record_offloading(state, request)
        except Exception:
            logger.warning("Transcript pre-model bookkeeping failed", exc_info=True)

        stream_message_id = str(uuid.uuid7())
        sniffer = _DeltaHandler(state, namespace, stream_message_id)
        manager = _attach(sniffer)
        try:
            response = await handler(request)
        except BaseException as exc:
            _detach(manager, sniffer)
            # A subagent's model error surfaces to the parent as a failed
            # ``task`` tool call, which keeps running; only the root settles
            # the turn. A cancellation never reaches ``aafter_agent`` at all,
            # so the run's state and writer task are only freed here.
            if not namespace:
                if isinstance(exc, asyncio.CancelledError):
                    await self._interrupt_turn(state)
                else:
                    await self._fail_turn(state, exc)
            raise
        _detach(manager, sniffer)
        try:
            self._complete_messages(state, namespace, response, sniffer)
        except Exception:
            logger.warning("Transcript message completion failed", exc_info=True)
        return response

    def _record_injected_humans(self, state: RunState, request: ModelRequest) -> None:
        """Human messages the queue injected mid-run are not in ``turn.requested``."""
        for message in request.messages:
            if not isinstance(message, HumanMessage):
                continue
            if message.additional_kwargs.get("lc_source") == "summarization":
                continue
            message_id = message.id
            if not isinstance(message_id, str) or message_id in state.seen_human_ids:
                continue
            state.seen_human_ids.add(message_id)
            text = _transcribed_human_text(message)
            attachments, pending = _human_attachments(message, message_id)
            if not text and not attachments:
                continue
            state.enqueue(
                Command(
                    command_id=f"human:{message_id}",
                    event=MessageCompleted(
                        type="message.completed",
                        turn_id=state.turn_id,
                        message_id=message_id,
                        namespace=[],
                        role="human",
                        text=text,
                        reasoning="",
                        attachments=attachments or None,
                        created_at=datetime.now(UTC),
                    ),
                    actor_kind="user",
                    run_id=state.run_id,
                    turn_id=state.turn_id,
                    attachments=pending,
                )
            )

    def _record_offloading(self, state: RunState, request: ModelRequest) -> None:
        """Persist the offloading hint the summarizer already wrote into state.

        ``ConversationOffloadingMiddleware`` streams its status through
        ``get_stream_writer`` and mirrors it onto ``state``; reading it here
        keeps that middleware untouched.
        """
        status = request.state.get("conversation_offloading")
        if not isinstance(status, Mapping):
            return
        payload = _json_object(status)
        if state.offloading_notice == payload:
            return
        state.offloading_notice = payload
        state.enqueue(
            Command(
                command_id=str(uuid.uuid7()),
                event=RunNotice(
                    type="run.notice",
                    turn_id=state.turn_id,
                    kind="conversation_offloading",
                    data=payload,
                ),
                actor_kind="agent",
                run_id=state.run_id,
                turn_id=state.turn_id,
            )
        )

    def _complete_messages(
        self,
        state: RunState,
        namespace: list[str],
        response: ModelResponse,
        sniffer: _DeltaHandler,
    ) -> None:
        """Replace the streamed accumulation with the model's canonical output.

        The reply keeps the id its fragments used, and the graph's own id is
        aliased to it so a tool call it issued still points at the same row.
        """
        ai_messages = [message for message in response.result if isinstance(message, AIMessage)]
        streamed_id = sniffer.message_id if sniffer.streamed else None
        for index, message in enumerate(ai_messages):
            final_id = message.id if isinstance(message.id, str) and message.id else None
            message_id = streamed_id if index == 0 and streamed_id else final_id
            if message_id is None:
                continue
            if final_id is not None and final_id != message_id:
                state.message_alias[final_id] = message_id
            _flush_message(state, message_id, namespace, final=True)
            state.buffers.pop(message_id, None)
            state.enqueue(
                Command(
                    command_id=f"message:{message_id}:completed",
                    event=MessageCompleted(
                        type="message.completed",
                        turn_id=state.turn_id,
                        message_id=message_id,
                        namespace=list(namespace),
                        role="ai",
                        text=_message_text(message).strip(),
                        reasoning=_reasoning_text(message).strip(),
                        usage=_usage(message),
                        created_at=datetime.now(UTC),
                    ),
                    actor_kind="agent",
                    run_id=state.run_id,
                    turn_id=state.turn_id,
                )
            )
        if streamed_id is not None and not ai_messages:
            _flush_message(state, streamed_id, namespace, final=True)
        state.buffers.pop(sniffer.message_id, None)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | GraphCommand[Any]]],
    ) -> ToolMessage | GraphCommand[Any]:
        state = _lookup_state()
        tool_call = request.tool_call
        tool_call_id = tool_call.get("id")
        if not state.enabled or not isinstance(tool_call_id, str) or not tool_call_id:
            return await handler(request)

        namespace = current_namespace()
        name = tool_call.get("name") or "tool"
        try:
            state.enqueue(
                Command(
                    command_id=f"tool:{tool_call_id}:started",
                    event=ToolStarted(
                        type="tool.started",
                        turn_id=state.turn_id,
                        tool_call_id=tool_call_id,
                        message_id=_issuing_message_id(state, request, tool_call_id),
                        name=name,
                        input=_json_object(tool_call.get("args")),
                        namespace=namespace,
                    ),
                    actor_kind="agent",
                    run_id=state.run_id,
                    turn_id=state.turn_id,
                )
            )
        except Exception:
            logger.warning("Transcript tool start failed", exc_info=True, extra={"tool_name": name})

        token = _namespace.set((*_namespace.get(), tool_call_id))
        try:
            result = await handler(request)
        except asyncio.CancelledError:
            # A stopped tool never finished, and it did not fail either: leaving
            # it open is what keeps the reader showing the work the run was in
            # the middle of, the way the turn's own interruption describes it.
            # Cancelling a run that is awaiting a tool never reaches
            # ``aafter_agent``, so the root run settles its turn and releases
            # its writer here. A nested cancellation belongs to the ``task``
            # tool call above it, whose parent run keeps going.
            if not namespace:
                await self._interrupt_turn(state)
            raise
        except Exception as exc:
            self._complete_tool(
                state,
                tool_call_id,
                namespace,
                "error",
                *_tool_output(f"{type(exc).__name__}: {exc}"),
            )
            raise
        finally:
            _namespace.reset(token)
        self._complete_tool(state, tool_call_id, namespace, *_result_output(result, tool_call_id))
        return result

    def _complete_tool(
        self,
        state: RunState,
        tool_call_id: str,
        namespace: list[str],
        status: Literal["completed", "error"],
        text: str,
        truncated: bool,
    ) -> None:
        try:
            state.enqueue(
                Command(
                    command_id=f"tool:{tool_call_id}:completed",
                    event=ToolCompleted(
                        type="tool.completed",
                        turn_id=state.turn_id,
                        tool_call_id=tool_call_id,
                        status=status,
                        output_preview=text[:TOOL_OUTPUT_PREVIEW_CHARS] or None,
                        output_truncated=truncated,
                        has_output=bool(text),
                        namespace=namespace,
                    ),
                    actor_kind="agent",
                    run_id=state.run_id,
                    turn_id=state.turn_id,
                    tool_output=text,
                )
            )
        except Exception:
            logger.warning(
                "Transcript tool completion failed",
                exc_info=True,
                extra={"transcript_tool_call_id": tool_call_id},
            )

    async def _interrupt_turn(self, state: RunState) -> None:
        """End a cancelled turn. The cancel endpoint writes the same command id."""
        if state.terminal:
            return
        state.terminal = True
        _announce_run_end(state.thread_id, state.run_id)
        try:
            state.enqueue(
                Command(
                    command_id=f"turn:{state.turn_id}:interrupted",
                    event=TurnInterrupted(
                        type="turn.interrupted", turn_id=state.turn_id, run_id=state.run_id
                    ),
                    actor_kind="agent",
                    run_id=state.run_id,
                    turn_id=state.turn_id,
                )
            )
            await _finish(state)
        except Exception:
            logger.warning("Transcript turn interrupt append failed", exc_info=True)

    async def _fail_turn(self, state: RunState, exc: BaseException) -> None:
        if state.terminal:
            return
        state.terminal = True
        _announce_run_end(state.thread_id, state.run_id)
        try:
            state.enqueue(
                Command(
                    command_id=f"turn:{state.turn_id}:failed",
                    event=TurnFailed(
                        type="turn.failed",
                        turn_id=state.turn_id,
                        run_id=state.run_id,
                        error=f"{type(exc).__name__}: {exc}"[:ERROR_TEXT_CAP],
                    ),
                    actor_kind="agent",
                    run_id=state.run_id,
                    turn_id=state.turn_id,
                )
            )
            await _finish(state)
        except Exception:
            logger.warning("Transcript turn failure append failed", exc_info=True)

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        del state, runtime
        run_state = _lookup_state()
        if _namespace.get():
            return None
        _announce_run_end(run_state.thread_id, run_state.run_id)
        if not run_state.enabled:
            # An untranscribed thread still leaves a registry entry behind to
            # keep the rest of the run from re-checking; drop it here.
            _runs.pop(_run_key(run_state.thread_id, run_state.run_id), None)
            return None
        try:
            if not run_state.terminal:
                run_state.terminal = True
                run_state.enqueue(
                    Command(
                        command_id=f"turn:{run_state.turn_id}:completed",
                        event=TurnCompleted(
                            type="turn.completed",
                            turn_id=run_state.turn_id,
                            run_id=run_state.run_id,
                        ),
                        actor_kind="agent",
                        run_id=run_state.run_id,
                        turn_id=run_state.turn_id,
                    )
                )
            await _finish(run_state)
        except Exception:
            logger.warning("Transcript turn completion failed", exc_info=True)
        return None


def _parse_turn_id(raw: str | None) -> UUID | None:
    if raw is None:
        return None
    try:
        return UUID(raw)
    except ValueError:
        logger.warning("Unparseable transcript_turn_id, minting a new turn")
        return None


def _sender(human: HumanMessage, ids: RunIds, metadata: Mapping[str, object]) -> MessageSender:
    """Who asked for this turn, from the message envelope then the run config."""
    login = (
        message_sender_id(human.content, kind="human")
        or _string(ids.configurable.get("github_login"))
        or _string(metadata.get("owner_login"))
        or "unknown"
    )
    kind = _string(ids.configurable.get("source")) or _string(metadata.get("source")) or "unknown"
    return MessageSender(login=login, kind=kind)


def _turn_requested(
    state: RunState, human: HumanMessage, ids: RunIds, metadata: Mapping[str, object]
) -> Command:
    message_id = human.id if isinstance(human.id, str) and human.id else str(uuid.uuid7())
    # ``_start_run`` marks this message as seen, so its images can never be
    # recovered by ``_record_injected_humans``; they travel with the request.
    attachments, pending = _human_attachments(human, message_id)
    return Command(
        command_id=f"turn:{state.turn_id}:requested",
        event=TurnRequested(
            type="turn.requested",
            turn_id=state.turn_id,
            message_id=message_id,
            text=_transcribed_human_text(human),
            sender=_sender(human, ids, metadata),
            attachments=attachments,
            model_id=_string(ids.configurable.get("resolved_agent_model_id"))
            or _string(ids.configurable.get("agent_model_id")),
            effort=_string(ids.configurable.get("agent_effort")),
        ),
        actor_kind="user",
        run_id=state.run_id,
        turn_id=state.turn_id,
        attachments=pending,
    )


def _issuing_message_id(state: RunState, request: ToolCallRequest, tool_call_id: str) -> str | None:
    candidates = request.state.get("messages") if isinstance(request.state, Mapping) else None
    if not isinstance(candidates, Sequence):
        return None
    for message in reversed(candidates):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or ():
            if call.get("id") != tool_call_id:
                continue
            if not isinstance(message.id, str):
                return None
            return state.message_alias.get(message.id, message.id)
    return None


def _json_object(value: object) -> JsonObject:
    """``value`` as a JSON object, dropping keys pydantic could not encode."""
    if not isinstance(value, Mapping):
        return {} if value is None else {"value": repr(value)}
    out: JsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        try:
            out[key] = cast(JsonValue, to_jsonable_python(item))
        except PydanticSerializationError:
            logger.debug("Dropping unserializable transcript payload key", exc_info=True)
    return out


def _result_output(
    result: ToolMessage | GraphCommand[Any], tool_call_id: str
) -> tuple[Literal["completed", "error"], str, bool]:
    """The tool's status, its capped output text, and whether capping cut it."""
    if isinstance(result, ToolMessage):
        message: ToolMessage | None = result
    else:
        update = result.update
        updates = update.get("messages") if isinstance(update, Mapping) else None
        message = next(
            (
                item
                for item in (updates if isinstance(updates, Sequence) else ())
                if isinstance(item, ToolMessage) and item.tool_call_id == tool_call_id
            ),
            None,
        )
    if message is None:
        return "completed", "", False
    status: Literal["completed", "error"] = "error" if message.status == "error" else "completed"
    return status, *_tool_output(message.content)


def _attach(handler: AsyncCallbackHandler) -> CallbackManager | AsyncCallbackManager | None:
    """Add ``handler`` to the node's callback manager so it sees model deltas.

    The model node invokes with no explicit config, so the model inherits the
    contextvar ``RunnableConfig``; its ``callbacks`` manager is where a handler
    has to live to receive the child LLM run's token callbacks.
    """
    try:
        callbacks = get_config().get("callbacks")
    except RuntimeError:
        return None
    if not isinstance(callbacks, (CallbackManager, AsyncCallbackManager)):
        logger.debug("No callback manager on the run config; delta capture disabled")
        return None
    try:
        callbacks.add_handler(handler, inherit=True)
    except Exception:
        logger.warning("Could not attach transcript delta handler", exc_info=True)
        return None
    return callbacks


def _detach(
    manager: CallbackManager | AsyncCallbackManager | None, handler: AsyncCallbackHandler
) -> None:
    if manager is None:
        return
    try:
        manager.remove_handler(handler)
    except Exception:
        logger.warning("Could not detach transcript delta handler", exc_info=True)
