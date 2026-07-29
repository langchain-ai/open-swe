from decimal import Decimal
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage

from agent.utils.run_usage import summarize_run_usage


def _message(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    total_cost: float | None = None,
) -> AIMessage:
    usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    if total_cost is not None:
        usage["total_cost"] = total_cost
    return AIMessage(
        content="",
        response_metadata={"model_name": model},
        usage_metadata=cast(Any, usage),
    )


def test_summarize_run_usage_uses_only_latest_human_turn() -> None:
    state = {
        "messages": [
            HumanMessage(content="old"),
            _message(model="old-model", input_tokens=100, output_tokens=10, total_cost=0.1),
            HumanMessage(content="current"),
            _message(model="model-a", input_tokens=1_000, output_tokens=100, total_cost=0.02),
            _message(model="model-b", input_tokens=2_000, output_tokens=200, total_cost=0.03),
        ]
    }

    summary = summarize_run_usage(state)

    assert summary is not None
    assert summary.models == ("model-a", "model-b")
    assert summary.total_tokens == 3_300
    assert summary.total_cost == Decimal("0.05")


def test_summarize_run_usage_ignores_messages_without_usage() -> None:
    complete = _message(model="model-a", input_tokens=100, output_tokens=10, total_cost=0.01)
    incomplete = AIMessage(
        content="",
        response_metadata={"model_name": "model-b"},
    )

    summary = summarize_run_usage(
        {"messages": [HumanMessage(content="current"), complete, incomplete]}
    )

    assert summary is not None
    assert summary.models == ("model-a", "model-b")
    assert summary.total_tokens == 110
    assert summary.total_cost == Decimal("0.01")


def test_summarize_run_usage_returns_none_without_reported_usage_or_model() -> None:
    assert (
        summarize_run_usage({"messages": [HumanMessage(content="hi"), AIMessage(content="")]})
        is None
    )
