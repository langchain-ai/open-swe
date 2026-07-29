from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage


@dataclass(frozen=True)
class RunUsageSummary:
    models: tuple[str, ...]
    total_tokens: int | None
    total_cost: Decimal | None


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _tokens(usage: Any) -> int | None:
    if not isinstance(usage, dict):
        return None
    total = _number(usage.get("total_tokens"))
    if total is not None:
        return total
    input_tokens = _number(usage.get("input_tokens"))
    output_tokens = _number(usage.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    return input_tokens + output_tokens


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _cost(values: Any) -> Decimal | None:
    if not isinstance(values, dict):
        return None
    total = _decimal(values.get("total_cost"))
    if total is not None:
        return total
    input_cost = _decimal(values.get("input_cost"))
    output_cost = _decimal(values.get("output_cost"))
    if input_cost is None or output_cost is None:
        return None
    return input_cost + output_cost


def _message_cost(message: AIMessage) -> Decimal | None:
    cost = _cost(message.usage_metadata)
    if cost is not None:
        return cost
    metadata = message.response_metadata
    cost = _cost(metadata)
    if cost is not None:
        return cost
    for key in ("usage", "token_usage", "usage_metadata"):
        cost = _cost(metadata.get(key))
        if cost is not None:
            return cost
    return None


def _message_model(message: AIMessage) -> str | None:
    metadata = message.response_metadata
    for key in ("model_name", "model", "model_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def summarize_run_usage(state: dict[str, Any] | None) -> RunUsageSummary | None:
    """Summarize model-reported usage since the latest human message."""
    if not isinstance(state, dict):
        return None
    messages = state.get("messages")
    if not isinstance(messages, list):
        return None
    start = 0
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            start = index + 1
            break
    ai_messages = [message for message in messages[start:] if isinstance(message, AIMessage)]
    if not ai_messages:
        return None

    models = {model for message in ai_messages if (model := _message_model(message))}
    reported = [
        (tokens, _message_cost(message))
        for message in ai_messages
        if (tokens := _tokens(message.usage_metadata)) is not None
    ]
    total_tokens = sum(tokens for tokens, _cost_value in reported) if reported else None
    total_cost = (
        sum((cost for _tokens_value, cost in reported if cost is not None), Decimal(0))
        if reported and all(cost is not None for _tokens_value, cost in reported)
        else None
    )
    if not models and total_tokens is None and total_cost is None:
        return None
    return RunUsageSummary(
        models=tuple(sorted(models)),
        total_tokens=total_tokens,
        total_cost=total_cost,
    )
