from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage


@dataclass(frozen=True)
class RunUsageSummary:
    models: tuple[str, ...]
    main_agent_tokens: int | None


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
    reported_tokens = [
        tokens for message in ai_messages if (tokens := _tokens(message.usage_metadata)) is not None
    ]
    main_agent_tokens = sum(reported_tokens) if reported_tokens else None
    if not models and main_agent_tokens is None:
        return None
    return RunUsageSummary(
        models=tuple(sorted(models)),
        main_agent_tokens=main_agent_tokens,
    )
