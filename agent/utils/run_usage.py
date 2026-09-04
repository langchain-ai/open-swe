from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage


@dataclass(frozen=True)
class RunUsageSummary:
    models: tuple[str, ...]
    main_agent_tokens: int | None
    session_cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class _TokenCounts:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _tokens(usage: Any) -> _TokenCounts | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = _number(usage.get("input_tokens"))
    output_tokens = _number(usage.get("output_tokens"))
    total_tokens = _number(usage.get("total_tokens"))
    if total_tokens is None:
        if input_tokens is None or output_tokens is None:
            return None
        total_tokens = input_tokens + output_tokens
    input_details = usage.get("input_token_details")
    cache_read = (
        _number(input_details.get("cache_read")) if isinstance(input_details, dict) else None
    ) or 0
    return _TokenCounts(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=max(total_tokens - cache_read, 0),
    )


def _message_model(message: AIMessage) -> str | None:
    metadata = message.response_metadata
    for key in ("model_name", "model", "model_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def summarize_run_usage(state: dict[str, Any] | None) -> RunUsageSummary | None:
    """Summarize main-agent usage since the latest human message."""
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
        tokens for message in ai_messages if (tokens := _tokens(message.usage_metadata)) is not None
    ]
    total_tokens = sum(tokens.total_tokens for tokens in reported) if reported else None
    input_values = [tokens.input_tokens for tokens in reported if tokens.input_tokens is not None]
    output_values = [
        tokens.output_tokens for tokens in reported if tokens.output_tokens is not None
    ]
    if not models and total_tokens is None:
        return None
    return RunUsageSummary(
        models=tuple(sorted(models)),
        main_agent_tokens=total_tokens,
        input_tokens=sum(input_values) if input_values else None,
        output_tokens=sum(output_values) if output_values else None,
        total_tokens=total_tokens,
    )
