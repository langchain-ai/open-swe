from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage


def _message_invocation_id(message: AIMessage) -> str | None:
    metadata = message.response_metadata
    values = {
        value
        for key in ("open_swe_invocation_id", "open_swe_run_id")
        if isinstance((value := metadata.get(key)), str) and value
    }
    return next(iter(values)) if len(values) == 1 else None


@dataclass(frozen=True)
class RunUsageSummary:
    models: tuple[str, ...]
    total_tokens: int | None
    session_cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_effort: str | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


@dataclass(frozen=True)
class _TokenCounts:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None

    @classmethod
    def from_usage(cls, usage: object) -> _TokenCounts | None:
        if not isinstance(usage, dict):
            return None
        input_tokens = _number(usage.get("input_tokens"))
        output_tokens = _number(usage.get("output_tokens"))
        total_tokens = _number(usage.get("total_tokens"))
        if total_tokens is None:
            if input_tokens is None or output_tokens is None:
                return None
            total_tokens = input_tokens + output_tokens
        details = usage.get("input_token_details")
        details = details if isinstance(details, dict) else {}
        cache_read = _number(details.get("cache_read"))
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=max(total_tokens - (cache_read or 0), 0),
            cache_read_tokens=cache_read,
            cache_creation_tokens=_number(details.get("cache_creation")),
        )

    @staticmethod
    def sum(values: list[int | None]) -> int | None:
        """Sum the reported values; None when no message reported any."""
        known = [value for value in values if value is not None]
        return sum(known) if known else None


def _message_model(message: AIMessage) -> str | None:
    metadata = message.response_metadata
    for key in ("model_name", "model", "model_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def summarize_run_usage(
    state: dict[str, Any] | None,
    *,
    invocation_id: str | None = None,
) -> RunUsageSummary | None:
    """Summarize main-agent usage for one invocation or the latest human turn."""
    if not isinstance(state, dict):
        return None
    messages = state.get("messages")
    if not isinstance(messages, list):
        return None
    if invocation_id is not None:
        ai_messages = [
            message
            for message in messages
            if isinstance(message, AIMessage) and _message_invocation_id(message) == invocation_id
        ]
    else:
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
        tokens
        for message in ai_messages
        if (tokens := _TokenCounts.from_usage(message.usage_metadata)) is not None
    ]
    total_tokens = sum(tokens.total_tokens for tokens in reported) if reported else None
    if not models and total_tokens is None:
        return None
    return RunUsageSummary(
        models=tuple(sorted(models)),
        total_tokens=total_tokens,
        input_tokens=_TokenCounts.sum([tokens.input_tokens for tokens in reported]),
        output_tokens=_TokenCounts.sum([tokens.output_tokens for tokens in reported]),
        cache_read_tokens=_TokenCounts.sum([tokens.cache_read_tokens for tokens in reported]),
        cache_creation_tokens=_TokenCounts.sum(
            [tokens.cache_creation_tokens for tokens in reported]
        ),
    )
