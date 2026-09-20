"""Transcript middleware: paragraph batching and the emitted event sequence."""

import asyncio
import base64
import itertools
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid7

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks.manager import AsyncCallbackManager
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware import transcript as mw
from agent.transcript.engine import Command
from agent.transcript.events import MessageUsage, TurnFailed

THREAD_ID = "thread-under-test"
RUN_ID = "run-under-test"


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    mw._runs.clear()
    yield
    mw._runs.clear()


class FakeEngine:
    """Collects the commands the middleware appends, in order."""

    def __init__(self) -> None:
        self.commands: list[Command] = []

    async def append(self, thread_id: str, commands: Sequence[Command]) -> None:
        assert thread_id == THREAD_ID
        self.commands.extend(commands)

    @property
    def types(self) -> list[str]:
        return [command.event.type for command in self.commands]

    @property
    def command_ids(self) -> list[str]:
        return [command.command_id for command in self.commands]


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transcribed: bool,
    turn_id: UUID | None = None,
    postgres_configured: bool = True,
) -> FakeEngine:
    engine = FakeEngine()
    monkeypatch.setattr(mw, "append", engine.append)
    monkeypatch.setattr(mw.postgres, "configured", lambda: postgres_configured)

    async def _has_transcript(thread_id: str) -> bool:
        return transcribed

    monkeypatch.setattr(mw, "_has_transcript", _has_transcript)

    async def _turn_context(thread_id: str, turn_id: UUID) -> tuple[str | None, str | None]:
        return None, None

    monkeypatch.setattr(mw.checkpoints, "_turn_context", _turn_context)
    configurable: dict[str, Any] = {"thread_id": THREAD_ID, "run_id": RUN_ID}
    if turn_id is not None:
        configurable["transcript_turn_id"] = str(turn_id)
    monkeypatch.setattr(mw, "get_config", lambda: {"configurable": configurable})
    return engine


def _model_request(messages: list[Any], state: dict[str, Any] | None = None) -> ModelRequest:
    return ModelRequest(
        model=GenericFakeChatModel(messages=itertools.cycle([AIMessage(content="x")])),
        messages=messages,
        state=state if state is not None else {"messages": messages},
        runtime=None,
    )


def _tool_request(tool_call_id: str, state: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": "read_file", "args": {"path": "a.py"}, "id": tool_call_id},
        tool=None,
        state=state,
        runtime=None,
    )


# --- paragraph splitter ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("no boundary yet", 0),
        ("one line\nstill going", 0),
        ("para one\n\npara two", len("para one\n\n")),
        ("intro\n- first item", len("intro\n")),
        ("intro\n1. first item", len("intro\n")),
        ("```py\ncode\n\nmore\n", 0),
        ("```py\ncode\n```\ntail", len("```py\ncode\n```\n")),
        ("```py\n- not a list\n```\nx", len("```py\n- not a list\n```\n")),
        ("a\n\nb\n\nc", len("a\n\nb\n\n")),
    ],
)
def test_paragraph_boundary(text: str, expected: int) -> None:
    assert mw.paragraph_boundary(text) == expected


@pytest.mark.parametrize(
    ("last_flush", "pending", "now", "final", "fragment", "remaining"),
    [
        (0.0, "para one\n\npara two", mw.PARAGRAPH_FLUSH_SECONDS / 2, False, None, None),
        (
            0.0,
            "para one\n\npara two",
            mw.PARAGRAPH_FLUSH_SECONDS,
            False,
            "para one\n\n",
            "para two",
        ),
        (0.0, "a single unfinished paragraph", 10.0, False, None, None),
        (100.0, "x" * mw.HARD_FLUSH_CHARS, 100.0, False, "x" * mw.HARD_FLUSH_CHARS, ""),
        (100.0, "head\n\n" + "x" * mw.HARD_FLUSH_CHARS, 100.0, False, "head\n\n", None),
        (0.0, "trailing words", 0.0, True, "trailing words", ""),
    ],
)
def test_paragraph_buffer_flushes(
    last_flush: float,
    pending: str,
    now: float,
    final: bool,
    fragment: str | None,
    remaining: str | None,
) -> None:
    buffer = mw.ParagraphBuffer(last_flush=last_flush)
    buffer.add(pending)
    assert buffer.take(now, final=final) == fragment
    if remaining is not None:
        assert buffer.pending == remaining


# --- hook sequence ---------------------------------------------------------


async def test_hook_sequence_for_a_transcribed_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    turn_id = uuid7()
    engine = _install(monkeypatch, transcribed=True, turn_id=turn_id)
    middleware = mw.TranscriptMiddleware()
    human = HumanMessage(content="do the thing", id="human-1")
    state: dict[str, Any] = {"messages": [human]}

    await middleware.abefore_agent(state, None)

    ai = AIMessage(
        content="on it",
        id="ai-1",
        tool_calls=[{"name": "read_file", "args": {"path": "a.py"}, "id": "call-1"}],
        usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    )

    async def model_handler(request: ModelRequest) -> ModelResponse:
        return ModelResponse(result=[ai])

    await middleware.awrap_model_call(_model_request([human]), model_handler)

    async def tool_handler(request: ToolCallRequest) -> ToolMessage:
        assert mw.current_namespace() == ["call-1"]
        return ToolMessage(content="file body", tool_call_id="call-1")

    await middleware.awrap_tool_call(
        _tool_request("call-1", {"messages": [human, ai]}), tool_handler
    )
    await middleware.aafter_agent({"messages": [human, ai]}, None)

    assert engine.types == [
        "turn.started",
        "message.completed",
        "tool.started",
        "tool.completed",
        "turn.checkpoint.completed",
        "turn.completed",
    ]
    assert engine.command_ids[0] == f"turn:{turn_id}:started:{RUN_ID}"
    assert engine.command_ids[-1] == f"turn:{turn_id}:completed"
    assert engine.commands[1].event.usage == MessageUsage(
        input_tokens=120, output_tokens=30, total_tokens=150
    )
    started = engine.commands[2]
    assert started.event.message_id == "ai-1"
    assert started.event.namespace == []
    completed = engine.commands[3]
    assert completed.event.status == "completed"
    # The wire carries a preview; the full output rides beside the command.
    assert completed.event.output_preview == "file body"
    assert completed.event.has_output is True
    assert completed.event.output_truncated is False
    assert completed.tool_output == "file body"


@pytest.mark.parametrize(
    ("transcribed", "postgres_configured"),
    [
        # An older thread with agent turns but no transcript row, and a
        # deployment with nowhere to keep a transcript at all.
        (False, True),
        (False, False),
    ],
)
async def test_an_untranscribable_run_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, transcribed: bool, postgres_configured: bool
) -> None:
    engine = _install(monkeypatch, transcribed=transcribed, postgres_configured=postgres_configured)
    stamped: list[str] = []

    async def _stamp(thread_id: str) -> None:
        stamped.append(thread_id)

    monkeypatch.setattr(mw, "_stamp_transcript", _stamp)
    middleware = mw.TranscriptMiddleware()
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="hi", id="h"), AIMessage(content="hello", id="a")]
    }

    await middleware.abefore_agent(state, None)

    async def model_handler(request: ModelRequest) -> ModelResponse:
        return ModelResponse(result=[AIMessage(content="more", id="a2")])

    await middleware.awrap_model_call(_model_request(state["messages"]), model_handler)
    await middleware.aafter_agent(state, None)

    assert engine.commands == []
    assert stamped == []


async def test_only_mid_run_human_messages_are_recorded_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History belongs to turns that are over; only an injected message is new."""
    engine = _install(monkeypatch, transcribed=True, turn_id=uuid7())
    middleware = mw.TranscriptMiddleware()
    history = [
        HumanMessage(content="first ask", id="human-1"),
        AIMessage(content="done", id="ai-1"),
        HumanMessage(content="second ask", id="human-2"),
    ]
    await middleware.abefore_agent({"messages": history}, None)

    injected = HumanMessage(content="also do this", id="human-queued")

    async def model_handler(request: ModelRequest) -> ModelResponse:
        return ModelResponse(result=[AIMessage(content="ok", id="ai-2")])

    for _ in range(2):
        await middleware.awrap_model_call(_model_request([*history, injected]), model_handler)
    await middleware.aafter_agent({"messages": history}, None)

    human_events = [
        command for command in engine.commands if command.command_id.startswith("human:")
    ]
    assert [command.command_id for command in human_events] == ["human:human-queued"]
    assert human_events[0].event.role == "human"


async def test_a_human_message_keeps_the_envelope_it_is_attributed_by(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Who sent a message, on which surface, is carried by the envelope alone."""
    engine = _install(monkeypatch, transcribed=True, turn_id=None)
    middleware = mw.TranscriptMiddleware()
    entity = HumanMessage(
        content=(
            '<dynamic-context kind="person" id="slack:U1"><handle>bob</handle></dynamic-context>'
        ),
        id="entity-bob",
    )
    envelope = (
        '<input-message sender="slack:U1" surface="slack" kind="human">'
        "<content>add a greet() helper</content></input-message>"
    )
    human = HumanMessage(content=envelope, id="human-1")
    await middleware.abefore_agent({"messages": [entity, human]}, None)

    async def model_handler(request: ModelRequest) -> ModelResponse:
        return ModelResponse(result=[AIMessage(content="ok", id="ai-1")])

    await middleware.awrap_model_call(_model_request([entity, human]), model_handler)
    await middleware.aafter_agent({"messages": []}, None)

    requested = next(
        command.event for command in engine.commands if command.command_id.endswith(":requested")
    )
    assert requested.text == envelope
    # The introduction that names the sender is recorded too, though it renders
    # as nothing: without it the reader has no display name to attribute by.
    recorded = [command for command in engine.commands if command.command_id.startswith("human:")]
    assert [command.command_id for command in recorded] == ["human:entity-bob"]
    assert "slack:U1" in (recorded[0].event.text or "")


async def test_model_failure_records_turn_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _install(monkeypatch, transcribed=True, turn_id=uuid7())
    middleware = mw.TranscriptMiddleware()
    await middleware.abefore_agent({"messages": [HumanMessage(content="hi", id="h")]}, None)

    async def model_handler(request: ModelRequest) -> ModelResponse:
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError):
        await middleware.awrap_model_call(_model_request([]), model_handler)

    assert engine.types == ["turn.started", "turn.checkpoint.completed", "turn.failed"]
    failed = engine.commands[-1].event
    assert isinstance(failed, TurnFailed)
    assert "provider exploded" in failed.error


async def test_subagent_tool_calls_carry_the_parent_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _install(monkeypatch, transcribed=True, turn_id=uuid7())
    parent = mw.TranscriptMiddleware()
    subagent = mw.TranscriptMiddleware()
    await parent.abefore_agent({"messages": [HumanMessage(content="hi", id="h")]}, None)

    async def nested_tool(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="grepped", tool_call_id="inner-1")

    async def task_tool(request: ToolCallRequest) -> ToolMessage:
        await subagent.awrap_tool_call(_tool_request("inner-1", {"messages": []}), nested_tool)
        return ToolMessage(content="delegated", tool_call_id="task-1")

    await parent.awrap_tool_call(_tool_request("task-1", {"messages": []}), task_tool)
    await parent.aafter_agent({"messages": []}, None)

    namespaces = {
        command.event.tool_call_id: command.event.namespace
        for command in engine.commands
        if command.event.type == "tool.started"
    }
    assert namespaces == {"task-1": [], "inner-1": ["task-1"]}


async def test_streamed_fragments_keep_one_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chunk ids need not match the final message id (OpenAI Responses).

    The fragments and the canonical text have to land on the same row, and a
    tool call issued by that message has to point at the id they used.
    """
    engine = _install(monkeypatch, transcribed=True, turn_id=uuid7())
    manager = AsyncCallbackManager(handlers=[])
    monkeypatch.setattr(
        mw,
        "get_config",
        lambda: {
            "configurable": {"thread_id": THREAD_ID, "run_id": RUN_ID},
            "callbacks": manager,
        },
    )
    middleware = mw.TranscriptMiddleware()
    await middleware.abefore_agent({"messages": [HumanMessage(content="hi", id="h")]}, None)

    ai = AIMessage(
        content="Hello there",
        id="resp_final",
        tool_calls=[{"name": "read_file", "args": {}, "id": "call-1"}],
    )

    async def model_handler(request: ModelRequest) -> ModelResponse:
        sniffer = manager.handlers[-1]
        for token in ("Hello", " ", "there", "\n\n"):
            await sniffer.on_llm_new_token(
                token,
                chunk=ChatGenerationChunk(message=AIMessageChunk(content=token, id="lc_run--abc")),
                run_id=uuid7(),
            )
        return ModelResponse(result=[ai])

    await middleware.awrap_model_call(_model_request([]), model_handler)

    async def tool_handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="ok", tool_call_id="call-1")

    await middleware.awrap_tool_call(_tool_request("call-1", {"messages": [ai]}), tool_handler)
    await middleware.aafter_agent({"messages": []}, None)

    message_events = [
        command
        for command in engine.commands
        if command.event.type in {"message.appended", "message.completed"}
    ]
    used_ids = {command.event.message_id for command in message_events}
    assert len(used_ids) == 1
    assert used_ids != {"resp_final"}
    assert message_events[-1].event.type == "message.completed"
    assert message_events[-1].event.text == "Hello there"
    assert "".join(command.event.text or "" for command in message_events[:-1]) == "Hello there\n\n"
    tool_started = next(
        command for command in engine.commands if command.event.type == "tool.started"
    )
    assert tool_started.event.message_id == next(iter(used_ids))


async def test_an_external_turn_request_keeps_its_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack and GitHub runs are recorded by ``turn.requested`` alone."""
    engine = _install(monkeypatch, transcribed=True)
    middleware = mw.TranscriptMiddleware()
    human = HumanMessage(
        content=[
            {"type": "text", "text": "what is wrong here"},
            {
                "type": "image",
                "base64": base64.b64encode(b"pretend-png").decode("ascii"),
                "mime_type": "image/png",
                "file_name": "screenshot.png",
            },
        ],
        id="human-slack",
    )

    await middleware.abefore_agent({"messages": [AIMessage(content="old"), human]}, None)
    await middleware.aafter_agent({"messages": []}, None)

    assert engine.types[:2] == ["turn.requested", "turn.started"]
    requested = engine.commands[0]
    assert requested.event.message_id == "human-slack"
    assert len(requested.attachments) == 1
    assert requested.attachments[0].data == b"pretend-png"
    assert requested.event.attachments[0].file_name == "screenshot.png"
    assert requested.event.attachments[0].attachment_id == requested.attachments[0].attachment_id


async def test_tool_cancellation_settles_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling while a tool runs never reaches ``aafter_agent``."""
    turn_id = uuid7()
    engine = _install(monkeypatch, transcribed=True, turn_id=turn_id)
    middleware = mw.TranscriptMiddleware()
    await middleware.abefore_agent({"messages": [HumanMessage(content="hi", id="h")]}, None)

    async def cancelled_tool(request: ToolCallRequest) -> ToolMessage:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await middleware.awrap_tool_call(_tool_request("call-1", {"messages": []}), cancelled_tool)

    assert engine.types == ["turn.started", "tool.started", "tool.completed", "turn.interrupted"]
    assert engine.commands[2].event.status == "error"
    # The run's writer and registry entry are released, not left blocked.
    assert mw._runs == {}


async def test_a_cancelled_subagent_tool_leaves_the_parent_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _install(monkeypatch, transcribed=True, turn_id=uuid7())
    parent = mw.TranscriptMiddleware()
    subagent = mw.TranscriptMiddleware()
    await parent.abefore_agent({"messages": [HumanMessage(content="hi", id="h")]}, None)

    async def cancelled_tool(request: ToolCallRequest) -> ToolMessage:
        raise asyncio.CancelledError

    async def task_tool(request: ToolCallRequest) -> ToolMessage:
        with pytest.raises(asyncio.CancelledError):
            await subagent.awrap_tool_call(
                _tool_request("inner-1", {"messages": []}), cancelled_tool
            )
        return ToolMessage(content="recovered", tool_call_id="task-1")

    await parent.awrap_tool_call(_tool_request("task-1", {"messages": []}), task_tool)
    await parent.aafter_agent({"messages": []}, None)

    assert "turn.interrupted" not in engine.types
    assert engine.types[-1] == "turn.completed"


async def test_a_stamped_thread_always_has_its_thread_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The UI switches reader on the stamp alone, so the row is committed first."""
    _install(monkeypatch, transcribed=False)
    order: list[str] = []
    middleware = mw.TranscriptMiddleware()

    async def _metadata(thread_id: str) -> dict[str, object]:
        return {}

    async def _stamp(thread_id: str) -> None:
        order.append("stamp")

    async def _append(thread_id: str, commands: Sequence[Command]) -> None:
        order.extend(command.event.type for command in commands)

    monkeypatch.setattr(mw, "_thread_metadata", _metadata)
    monkeypatch.setattr(mw, "_stamp_transcript", _stamp)
    monkeypatch.setattr(mw, "append", _append)

    state: dict[str, Any] = {"messages": [HumanMessage(content="hi", id="h")]}
    await middleware.abefore_agent(state, None)
    await middleware.aafter_agent(state, None)
    assert order[:2] == ["thread.created", "stamp"]

    async def _refuses(thread_id: str, commands: Sequence[Command]) -> None:
        raise RuntimeError("no database")

    mw._runs.clear()
    order.clear()
    monkeypatch.setattr(mw, "append", _refuses)
    await middleware.abefore_agent(state, None)

    assert order == []
    assert mw._lookup_state().enabled is False
