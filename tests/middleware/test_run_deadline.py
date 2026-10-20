"""One invocation budget survives checkpoints and bounds model/tool handlers."""

import asyncio
import time
from collections.abc import Awaitable, Callable

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatResult
from langchain_core.tools import tool
from langgraph.runtime import Runtime

from agent.middleware.run_deadline import (
    RunDeadlineExceeded,
    RunDeadlineMiddleware,
    RunDeadlineState,
)
from agent.middleware.trace import OpenSWEMiddleware


async def test_deadline_survives_restart_and_new_invocation_gets_new_budget() -> None:
    first = RunDeadlineMiddleware(invocation_id="first", timeout_seconds=10)
    update = await first.abefore_agent({"messages": []}, Runtime())
    initial_deadline = update["run_deadline_at"]
    assert isinstance(initial_deadline, float)
    state: RunDeadlineState = {
        "messages": [],
        "run_deadline_for": "first",
        "run_deadline_at": initial_deadline,
    }
    resumed = RunDeadlineMiddleware(invocation_id="first", timeout_seconds=100)
    assert await resumed.abefore_agent(state, Runtime()) == update
    subsequent = RunDeadlineMiddleware(invocation_id="next", timeout_seconds=100)
    fresh = await subsequent.abefore_agent(state, Runtime())
    fresh_deadline = fresh["run_deadline_at"]
    assert isinstance(fresh_deadline, float)
    assert fresh_deadline > initial_deadline


async def test_handler_is_cancelled_and_subsequent_calls_never_start() -> None:
    middleware = RunDeadlineMiddleware(invocation_id="one", timeout_seconds=0.02)
    await middleware.abefore_agent({"messages": []}, Runtime())
    cancelled = asyncio.Event()

    async def hanging() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    with pytest.raises(RunDeadlineExceeded):
        await middleware._call(hanging)
    assert cancelled.is_set()

    async def forbidden() -> None:
        raise AssertionError("A new operation started after budget exhaustion")

    with pytest.raises(RunDeadlineExceeded):
        await middleware._call(forbidden)


async def test_inner_timeout_keeps_its_original_meaning() -> None:
    middleware = RunDeadlineMiddleware(invocation_id="one", timeout_seconds=10)
    await middleware.abefore_agent({"messages": []}, Runtime())

    async def provider_timeout() -> None:
        raise TimeoutError("provider timeout")

    with pytest.raises(TimeoutError, match="provider timeout"):
        await middleware._call(provider_timeout)


async def test_expired_checkpoint_does_not_restart_the_clock() -> None:
    middleware = RunDeadlineMiddleware(invocation_id="one", timeout_seconds=60)
    await middleware.abefore_agent(
        {"messages": [], "run_deadline_for": "one", "run_deadline_at": time.time() - 1}, Runtime()
    )
    with pytest.raises(RunDeadlineExceeded):
        await middleware._call(lambda: asyncio.sleep(0))


async def test_resume_uses_checkpoint_even_when_before_agent_node_is_skipped() -> None:
    middleware = RunDeadlineMiddleware(invocation_id="one", timeout_seconds=60)
    with pytest.raises(RunDeadlineExceeded):
        await middleware._call(
            lambda: asyncio.sleep(0),
            {"run_deadline_at": time.time() - 1, "run_deadline_for": "one"},
        )


async def test_legacy_checkpoint_gets_one_budget_without_before_agent() -> None:
    middleware = RunDeadlineMiddleware(timeout_seconds=0.01)
    await middleware._call(lambda: asyncio.sleep(0), {"messages": []})
    await asyncio.sleep(0.02)
    with pytest.raises(RunDeadlineExceeded):
        await middleware._call(lambda: asyncio.sleep(0), {"messages": []})


async def test_graph_propagates_deadline_and_cancels_model(monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled = asyncio.Event()

    async def hanging_generate(*args: object, **kwargs: object) -> ChatResult:
        try:
            await asyncio.sleep(60)
            raise AssertionError("model should have been cancelled")
        finally:
            cancelled.set()

    monkeypatch.setattr(FakeListChatModel, "_agenerate", hanging_generate)
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        middleware=[RunDeadlineMiddleware(timeout_seconds=0.1)],
    )
    with pytest.raises(RunDeadlineExceeded):
        await graph.ainvoke({"messages": [HumanMessage(content="hello")]})
    assert cancelled.is_set()


async def test_graph_propagates_deadline_and_cancels_tool() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    @tool
    async def wait_for_service() -> str:
        """Wait for the test service to respond."""
        entered.set()
        try:
            await asyncio.sleep(60)
            return "late result"
        finally:
            cancelled.set()

    class RequestToolMiddleware(OpenSWEMiddleware[RunDeadlineState]):
        async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        ) -> ModelResponse:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "wait_for_service", "args": {}, "id": "call-1"}],
                    )
                ]
            )

    middleware: list[OpenSWEMiddleware[RunDeadlineState]] = [
        RunDeadlineMiddleware(timeout_seconds=0.5),
        RequestToolMiddleware(),
    ]
    graph = create_agent(
        model=FakeListChatModel(responses=["unused"]),
        tools=[wait_for_service],
        middleware=middleware,
    )
    with pytest.raises(RunDeadlineExceeded):
        await graph.ainvoke({"messages": [HumanMessage(content="hello")]})
    assert entered.is_set()
    assert cancelled.is_set()
