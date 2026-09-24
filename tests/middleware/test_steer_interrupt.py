"""Steering a run cuts its running command short."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.store.memory import InMemoryStore

from agent.middleware import steer_interrupt
from agent.middleware.steer_interrupt import SteerInterruptMiddleware

THREAD_ID = "thread-1"


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steer_interrupt, "STEER_POLL_SECONDS", 0.01)


def _request(store: InMemoryStore, name: str = "execute") -> ToolCallRequest:
    runtime = SimpleNamespace(store=store, config={"configurable": {"thread_id": THREAD_ID}})
    return cast(
        ToolCallRequest,
        SimpleNamespace(tool_call={"id": "call-1", "name": name, "args": {}}, runtime=runtime),
    )


async def _queue(store: InMemoryStore, *contents: dict[str, Any]) -> None:
    await store.aput(
        ("queue", THREAD_ID),
        "pending_messages",
        {"messages": [{"content": content} for content in contents]},
    )


def _steer(queue_id: str) -> dict[str, Any]:
    return {"text": "stop that", "queue_id": queue_id, "steer": True}


class _Command:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def __call__(self, request: ToolCallRequest) -> ToolMessage:
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ToolMessage(content="done", tool_call_id="call-1")


async def test_a_new_steer_interrupts_the_command() -> None:
    store = InMemoryStore()
    command = _Command()
    call = asyncio.ensure_future(
        SteerInterruptMiddleware().awrap_tool_call(_request(store), command)
    )
    await command.started.wait()
    await _queue(store, _steer("msg-1"))

    result = await asyncio.wait_for(call, timeout=5)

    assert command.cancelled
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call-1"


async def test_only_steers_queued_after_the_command_started_interrupt_it() -> None:
    """Otherwise a steer that no model call consumes, as in a subagent, stops every command."""
    store = InMemoryStore()
    await _queue(store, _steer("msg-0"))
    command = _Command()
    call = asyncio.ensure_future(
        SteerInterruptMiddleware().awrap_tool_call(_request(store), command)
    )
    await command.started.wait()
    await _queue(store, _steer("msg-0"), {"text": "someone joined", "queue_id": "note"})
    await asyncio.sleep(0.1)

    assert not call.done()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert command.cancelled


async def test_other_tools_are_not_interrupted() -> None:
    store = InMemoryStore()
    command = _Command()
    call = asyncio.ensure_future(
        SteerInterruptMiddleware().awrap_tool_call(_request(store, "write_file"), command)
    )
    await command.started.wait()
    await _queue(store, _steer("msg-1"))
    await asyncio.sleep(0.1)

    assert not call.done()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
