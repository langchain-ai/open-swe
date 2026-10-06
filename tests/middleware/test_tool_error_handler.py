import asyncio
from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

import pytest
from deepagents import create_deep_agent
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.errors import NodeCancelledError
from langgraph.graph import StateGraph, add_messages

from agent.middleware.tool_error_handler import ToolErrorMiddleware


class _State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class _DelegatingModel(BaseChatModel):
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "delegating-test"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls += 1
        message = (
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "task-1",
                        "name": "task",
                        "args": {"description": "wait", "subagent_type": "waiter"},
                    }
                ],
            )
            if self.calls == 1
            else AIMessage(content="continued after cancellation")
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _agent(subagent: StateGraph, model: _DelegatingModel):
    return create_deep_agent(
        model=model,
        tools=[],
        subagents=[
            {
                "name": "waiter",
                "description": "Wait until cancelled.",
                "runnable": subagent.compile(),
            }
        ],
        middleware=[ToolErrorMiddleware()],
    )


@pytest.mark.asyncio
async def test_subagent_node_cancellation_ends_run() -> None:
    async def cancel(_state: _State) -> _State:
        raise asyncio.CancelledError

    subagent = StateGraph(_State).add_node("cancel", cancel).set_entry_point("cancel")

    with pytest.raises(NodeCancelledError):
        await _agent(subagent, _DelegatingModel()).ainvoke(
            {"messages": [HumanMessage(content="delegate")]}
        )


@pytest.mark.asyncio
async def test_cancelling_run_stops_inflight_subagent() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def block(_state: _State) -> _State:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError

    subagent = StateGraph(_State).add_node("block", block).set_entry_point("block")
    parent_model = _DelegatingModel()
    run = asyncio.create_task(
        _agent(subagent, parent_model).ainvoke({"messages": [HumanMessage(content="delegate")]})
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    run.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run

    assert cancelled.is_set()
    assert parent_model.calls == 1
