from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.middleware.context_budget import (
    _BUDGET_MARKER,
    CONTEXT_BUDGET_MESSAGE,
    ContextBudgetMiddleware,
)


def _runtime() -> MagicMock:
    return MagicMock()


def test_under_budget_returns_none() -> None:
    middleware = ContextBudgetMiddleware(max_input_tokens=1_000_000)
    state = {"messages": [HumanMessage(content="hi")]}
    assert middleware.before_model(state, _runtime()) is None


def test_reported_usage_metadata_triggers_jump_to_end() -> None:
    middleware = ContextBudgetMiddleware(max_input_tokens=1_000)
    ai = AIMessage(content="ok")
    ai.usage_metadata = {"input_tokens": 1_500, "output_tokens": 10, "total_tokens": 1_510}
    state = {"messages": [HumanMessage(content="hi"), ai]}

    result = middleware.before_model(state, _runtime())

    assert result is not None
    assert result["jump_to"] == "end"
    assert len(result["messages"]) == 1
    content = result["messages"][0].content
    assert _BUDGET_MARKER in content
    assert "1500" in content


def test_estimator_triggers_jump_to_end_for_long_history() -> None:
    middleware = ContextBudgetMiddleware(max_input_tokens=2_500)
    # 60_000 chars / 4 chars-per-token ≈ 15_000 tokens.
    long_text = "x" * 60_000
    state = {"messages": [HumanMessage(content=long_text)]}

    result = middleware.before_model(state, _runtime())

    assert result is not None
    assert result["jump_to"] == "end"
    assert _BUDGET_MARKER in result["messages"][0].content


def test_estimator_triggers_before_model_call_recursion_limit() -> None:
    # Synthetic alternating message/tool history that explodes the prompt long
    # before any plausible model-call recursion limit would catch it.
    middleware = ContextBudgetMiddleware(max_input_tokens=100_000)
    messages = [HumanMessage(content="seed")]
    for i in range(200):
        messages.append(AIMessage(content="thinking-" + "y" * 2_000))
        messages.append(HumanMessage(content=f"tool-output-{i}-" + "z" * 2_000))

    result = middleware.before_model({"messages": messages}, _runtime())

    assert result is not None
    assert result["jump_to"] == "end"


def test_already_signalled_returns_none() -> None:
    middleware = ContextBudgetMiddleware(max_input_tokens=10)
    marker_msg = AIMessage(content=f"{_BUDGET_MARKER}: already-signalled. {CONTEXT_BUDGET_MESSAGE}")
    state = {"messages": [HumanMessage(content="x" * 1_000), marker_msg]}

    assert middleware.before_model(state, _runtime()) is None


@pytest.mark.asyncio
async def test_after_agent_posts_slack_when_marker_present() -> None:
    middleware = ContextBudgetMiddleware()
    state = {
        "messages": [AIMessage(content=f"{_BUDGET_MARKER}: too big. {CONTEXT_BUDGET_MESSAGE}")]
    }

    with (
        patch(
            "agent.middleware.context_budget.get_config",
            return_value={
                "configurable": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}
            },
        ),
        patch(
            "agent.middleware.context_budget.post_slack_thread_reply",
            new_callable=AsyncMock,
        ) as mock_post,
    ):
        result = await middleware.aafter_agent(state, _runtime())

    assert result is None
    mock_post.assert_awaited_once()
    assert mock_post.await_args.args[0:2] == ("C1", "1.0")


@pytest.mark.asyncio
async def test_after_agent_noop_when_marker_absent() -> None:
    middleware = ContextBudgetMiddleware()
    state = {"messages": [AIMessage(content="all good")]}

    with patch(
        "agent.middleware.context_budget.post_slack_thread_reply",
        new_callable=AsyncMock,
    ) as mock_post:
        result = await middleware.aafter_agent(state, _runtime())

    assert result is None
    mock_post.assert_not_called()
