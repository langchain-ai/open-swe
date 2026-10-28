"""Slack response-format guidance derived from managed ingress context."""

from __future__ import annotations

import asyncio
from typing import Any, get_type_hints

import pytest
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from managed_deepagents import _identity_runtime
from managed_deepagents._channels.response_format import (
    CHANNEL_RESPONSE_FORMAT_MIDDLEWARE_NAME,
    SLACK_MARKDOWN_INSTRUCTIONS,
    create_channel_response_format_middleware,
)


class _Request:
    def __init__(
        self,
        *,
        runtime: object,
        system_message: SystemMessage,
        messages: list[str] | None = None,
    ) -> None:
        self.runtime = runtime
        self.system_message = system_message
        self.messages = messages if messages is not None else []

    def override(self, **overrides: Any) -> _Request:
        return _Request(
            runtime=overrides.get("runtime", self.runtime),
            system_message=overrides.get("system_message", self.system_message),
            messages=overrides.get("messages", self.messages),
        )


def test_factory_has_concrete_return_type() -> None:
    return_type = get_type_hints(create_channel_response_format_middleware)["return"]

    assert return_type is not Any


def _set_ambient(
    monkeypatch: pytest.MonkeyPatch,
    configurable: dict[str, object] | None,
) -> None:
    def get_config() -> dict[str, object]:
        if configurable is None:
            raise RuntimeError
        return {"configurable": configurable}

    monkeypatch.setattr(_identity_runtime, "get_config", get_config)


def test_appends_slack_markdown_guidance_from_ambient_authenticated_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_ambient(
        monkeypatch,
        {"langgraph_auth_user": {"mda_source_provider": "slack"}},
    )
    existing = SystemMessage(
        content=[
            {
                "type": "text",
                "text": "You are helpful.",
                "cache_control": {"type": "ephemeral"},
            }
        ]
    )
    request = _Request(
        runtime=Runtime(),
        system_message=existing,
        messages=["hello"],
    )
    middleware = create_channel_response_format_middleware()
    seen: list[_Request] = []

    result = middleware.wrap_model_call(request, lambda updated: seen.append(updated) or "answer")

    assert result == "answer"
    assert middleware.name == CHANNEL_RESPONSE_FORMAT_MIDDLEWARE_NAME
    assert seen[0] is not request
    assert seen[0].messages == ["hello"]
    assert seen[0].system_message.content == [
        {
            "type": "text",
            "text": "You are helpful.",
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": SLACK_MARKDOWN_INSTRUCTIONS},
    ]
    assert request.system_message.content == existing.content


@pytest.mark.parametrize(
    "configurable",
    [
        {"langgraph_auth_user": {"mda_source_provider": "http"}},
        {"mda_source_provider": "slack"},
    ],
)
def test_leaves_non_slack_and_forged_context_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    configurable: dict[str, object],
) -> None:
    _set_ambient(monkeypatch, None)
    request = _Request(
        runtime={"configurable": configurable},
        system_message=SystemMessage("You are helpful."),
    )
    seen: list[_Request] = []

    create_channel_response_format_middleware().wrap_model_call(request, seen.append)

    assert seen == [request]


def test_wraps_async_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_ambient(
        monkeypatch,
        {"langgraph_auth_user": {"mda_source_provider": "slack"}},
    )
    request = _Request(
        runtime=Runtime(),
        system_message=SystemMessage("You are helpful."),
    )

    async def handler(updated: _Request) -> _Request:
        return updated

    updated = asyncio.run(
        create_channel_response_format_middleware().awrap_model_call(request, handler)
    )

    assert updated.system_message.content[-1] == {
        "type": "text",
        "text": SLACK_MARKDOWN_INSTRUCTIONS,
    }
