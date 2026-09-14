from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from agent.agent_cost import finalize_agent_invocation_usage
from agent.middleware.model_fallback import ModelFallbackMiddleware
from agent.middleware.record_run_usage import record_run_usage


def _message(input_tokens: int, output_tokens: int) -> AIMessage:
    return AIMessage(
        content="done",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


@pytest.mark.asyncio
async def test_records_whole_run_across_queued_human_messages() -> None:
    first = _message(100, 10)
    first.response_metadata["open_swe_run_id"] = "run-1"
    second = _message(200, 20)
    second.response_metadata["open_swe_run_id"] = "run-1"
    state: AgentState = {
        "messages": [
            HumanMessage(content="old"),
            _message(400, 40),
            HumanMessage(content="current"),
            first,
            HumanMessage(content="queued follow-up"),
            second,
        ]
    }
    with (
        patch(
            "agent.run_config.get_config",
            return_value={"configurable": {"thread_id": "thread-1", "prepare_run_id": "run-1"}},
        ),
        patch(
            "agent.agent_cost.record_agent_invocation_completion",
            new_callable=AsyncMock,
            return_value=True,
        ) as record,
        patch(
            "agent.agent_cost.schedule_agent_cost_refresh",
            new_callable=AsyncMock,
            return_value=True,
        ) as schedule,
        patch(
            "agent.agent_cost.mark_agent_invocation_cost_refresh_scheduled",
            new_callable=AsyncMock,
        ) as mark_scheduled,
    ):
        await record_run_usage.aafter_agent(state, cast(Runtime[Any], MagicMock()))

    usage = record.await_args.kwargs["usage"]
    assert usage.input_tokens == 300
    assert usage.output_tokens == 30
    assert usage.total_tokens == 330
    schedule.assert_awaited_once_with({"thread_id": "thread-1", "invocation_id": "run-1"})
    mark_scheduled.assert_awaited_once_with(invocation_id="run-1")


@pytest.mark.asyncio
async def test_retries_cost_scheduling_after_completion_was_recorded() -> None:
    with (
        patch(
            "agent.agent_cost.record_agent_invocation_completion",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "agent.agent_cost.agent_invocation_needs_cost_refresh",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.agent_cost.schedule_agent_cost_refresh",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ) as schedule,
        patch(
            "agent.agent_cost.mark_agent_invocation_cost_refresh_scheduled",
            new_callable=AsyncMock,
        ) as mark_scheduled,
    ):
        await finalize_agent_invocation_usage(
            invocation_id="run-1", thread_id="thread-1", state=None
        )
        await finalize_agent_invocation_usage(
            invocation_id="run-1", thread_id="thread-1", state=None
        )

    assert schedule.await_count == 2
    mark_scheduled.assert_awaited_once_with(invocation_id="run-1")


@pytest.mark.asyncio
async def test_tags_model_responses_with_run_id() -> None:
    response = ModelResponse(result=[_message(100, 10)])
    handler = AsyncMock(return_value=response)
    with patch(
        "agent.run_config.get_config",
        return_value={"configurable": {"thread_id": "thread-1", "prepare_run_id": "run-1"}},
    ):
        result = await record_run_usage.awrap_model_call(MagicMock(), handler)

    assert result.result[0].response_metadata["open_swe_invocation_id"] == "run-1"
    assert result.result[0].response_metadata["open_swe_run_id"] == "run-1"


@pytest.mark.asyncio
async def test_analytics_failure_does_not_replace_model_error() -> None:
    request = ModelRequest(model=MagicMock(), messages=[], state={"messages": []})
    error = RuntimeError("Provider failed")
    with (
        patch(
            "agent.run_config.get_config",
            return_value={"configurable": {"thread_id": "thread-1", "invocation_id": "run-1"}},
        ),
        patch(
            "agent.agent_cost.record_agent_invocation_completion",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Database unavailable"),
        ),
    ):
        with pytest.raises(RuntimeError) as raised:
            await record_run_usage.awrap_model_call(request, AsyncMock(side_effect=error))
    assert raised.value is error


@pytest.mark.asyncio
async def test_successful_fallback_is_not_recorded_as_a_failed_invocation() -> None:
    primary = FakeListChatModel(responses=["unused"])
    fallback = FakeListChatModel(responses=["Recovered with fallback"])
    graph = create_agent(
        model=primary,
        middleware=[record_run_usage, ModelFallbackMiddleware(fallback, backoff_schedule=(0.0,))],
    )
    original = FakeListChatModel._agenerate

    async def generate(model, *args, **kwargs):
        if model is primary:
            raise TimeoutError("Primary unavailable")
        return await original(model, *args, **kwargs)

    with (
        patch.object(FakeListChatModel, "_agenerate", generate),
        patch(
            "agent.agent_cost.record_agent_invocation_completion",
            new_callable=AsyncMock,
            return_value=True,
        ) as record,
        patch(
            "agent.agent_cost.schedule_agent_cost_refresh",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.agent_cost.mark_agent_invocation_cost_refresh_scheduled", new_callable=AsyncMock
        ),
    ):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="Do the task")]},
            {"configurable": {"thread_id": "thread-1", "invocation_id": "run-1"}},
        )
    assert result["messages"][-1].content == "Recovered with fallback"
    assert record.await_count == 1
    assert record.await_args.kwargs["status"] == "success"
