from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentState
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

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
async def test_records_latest_turn_and_schedules_cost_refresh() -> None:
    state: AgentState = {
        "messages": [
            HumanMessage(content="old"),
            _message(100, 10),
            HumanMessage(content="current"),
            _message(200, 20),
        ]
    }
    with (
        patch(
            "agent.run_config.get_config",
            return_value={"configurable": {"thread_id": "thread-1", "prepare_run_id": "run-1"}},
        ),
        patch(
            "agent.middleware.record_run_usage.record_agent_run_completion",
            new_callable=AsyncMock,
            return_value=True,
        ) as record,
        patch(
            "agent.middleware.record_run_usage.schedule_agent_cost_refresh",
            new_callable=AsyncMock,
        ) as schedule,
    ):
        await record_run_usage.aafter_agent(state, cast(Runtime[Any], MagicMock()))

    usage = record.await_args.kwargs["usage"]
    assert usage.input_tokens == 200
    assert usage.output_tokens == 20
    assert usage.total_tokens == 220
    schedule.assert_awaited_once_with({"thread_id": "thread-1", "run_id": "run-1"})
