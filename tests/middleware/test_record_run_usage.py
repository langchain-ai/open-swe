from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentState, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from agent.agent_cost import finalize_agent_run_usage
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
            "agent.agent_cost.record_agent_run_completion",
            new_callable=AsyncMock,
            return_value=True,
        ) as record,
        patch(
            "agent.agent_cost.schedule_agent_cost_refresh",
            new_callable=AsyncMock,
            return_value=True,
        ) as schedule,
        patch(
            "agent.agent_cost.mark_agent_cost_refresh_scheduled",
            new_callable=AsyncMock,
        ) as mark_scheduled,
    ):
        await record_run_usage.aafter_agent(state, cast(Runtime[Any], MagicMock()))

    usage = record.await_args.kwargs["usage"]
    assert usage.input_tokens == 300
    assert usage.output_tokens == 30
    assert usage.total_tokens == 330
    schedule.assert_awaited_once_with({"thread_id": "thread-1", "run_id": "run-1"})
    mark_scheduled.assert_awaited_once_with(run_id="run-1")


@pytest.mark.asyncio
async def test_retries_cost_scheduling_after_completion_was_recorded() -> None:
    with (
        patch(
            "agent.agent_cost.record_agent_run_completion",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "agent.agent_cost.agent_run_needs_cost_refresh",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.agent_cost.schedule_agent_cost_refresh",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ) as schedule,
        patch(
            "agent.agent_cost.mark_agent_cost_refresh_scheduled",
            new_callable=AsyncMock,
        ) as mark_scheduled,
    ):
        await finalize_agent_run_usage(run_id="run-1", thread_id="thread-1", state=None)
        await finalize_agent_run_usage(run_id="run-1", thread_id="thread-1", state=None)

    assert schedule.await_count == 2
    mark_scheduled.assert_awaited_once_with(run_id="run-1")


@pytest.mark.asyncio
async def test_tags_model_responses_with_run_id() -> None:
    response = ModelResponse(result=[_message(100, 10)])
    handler = AsyncMock(return_value=response)
    with patch(
        "agent.run_config.get_config",
        return_value={"configurable": {"thread_id": "thread-1", "prepare_run_id": "run-1"}},
    ):
        result = await record_run_usage.awrap_model_call(MagicMock(), handler)

    assert result.result[0].response_metadata["open_swe_run_id"] == "run-1"
