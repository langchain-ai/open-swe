"""Response-format guidance derived from managed channel context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import ContentBlock, SystemMessage, TextContentBlock

from managed_deepagents._channels.binding import resolve_channel_binding
from managed_deepagents._channels.messaging_registry import channel_provider_config_for_name
from managed_deepagents._identity_runtime import _field, resolve_runtime_configurable

CHANNEL_RESPONSE_FORMAT_MIDDLEWARE_NAME = "mda-channel-response-format"
SLACK_MARKDOWN_INSTRUCTIONS = (
    "<channel_response_format>\n"
    "Write your final response using standard Markdown. "
    "In prose, format URLs as explicit Markdown links, such as "
    "[label](https://example.com), or autolinks, such as <https://example.com>, "
    "rather than bare URLs. Preserve literal URLs inside code.\n"
    "</channel_response_format>"
)


SLACK_CONVERSATION_INSTRUCTIONS = (
    "Mention another Slack agent as <@USER_ID> on each message that needs its reply. "
    "Use its verified Slack user ID; a name or thread reply alone is insufficient. "
    "The recipient must allow bot triggers."
)


def _slack_conversation_instructions(allow_bot_triggers: bool) -> str:
    behavior = (
        "Bot triggers are enabled. Reply to bot messages only when they request action. "
        "Finish completed tasks without another bot mention."
        if allow_bot_triggers
        else "Bot triggers are disabled. Bot messages cannot trigger you, even with a mention. "
        "You can message another agent, but its reply cannot start your next turn."
    )
    return (
        f"<slack_conversation>\n{SLACK_CONVERSATION_INSTRUCTIONS}\n"
        f"{behavior}\n</slack_conversation>"
    )


def _is_slack_request(request: ModelRequest[None]) -> bool:
    configurable = resolve_runtime_configurable(request.runtime)
    user = (configurable or {}).get("langgraph_auth_user")
    return _field(user, "mda_source_provider") == "slack"


def _with_slack_instructions(
    request: ModelRequest[None], allow_bot_triggers: bool | None
) -> ModelRequest[None]:
    blocks: list[ContentBlock] = list(
        request.system_message.content_blocks if request.system_message is not None else []
    )
    blocks.append(TextContentBlock(type="text", text=SLACK_MARKDOWN_INSTRUCTIONS))
    if allow_bot_triggers is not None:
        blocks.append(
            TextContentBlock(type="text", text=_slack_conversation_instructions(allow_bot_triggers))
        )
    return request.override(system_message=SystemMessage(content_blocks=blocks))


def _format_request(request: ModelRequest[None]) -> ModelRequest[None]:
    if not _is_slack_request(request):
        return request
    binding = resolve_channel_binding(
        request.runtime.context, resolve_runtime_configurable(request.runtime)
    )
    config = channel_provider_config_for_name(binding["name"]) if binding else None
    allow_bot_triggers = config.get("allowBotTriggers") if config is not None else None
    return _with_slack_instructions(
        request, allow_bot_triggers if isinstance(allow_bot_triggers, bool) else None
    )


class _ChannelResponseFormatMiddleware(AgentMiddleware[AgentState[object], None, object]):
    @property
    def name(self) -> str:
        return CHANNEL_RESPONSE_FORMAT_MIDDLEWARE_NAME

    def wrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[object]],
    ) -> ModelCallResult[object]:
        return handler(_format_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelCallResult[object]:
        return await handler(_format_request(request))


def create_channel_response_format_middleware() -> AgentMiddleware[
    AgentState[object], None, object
]:
    """Add Slack formatting and conversation guidance to model calls from managed Slack ingress."""
    return _ChannelResponseFormatMiddleware()


__all__ = [
    "CHANNEL_RESPONSE_FORMAT_MIDDLEWARE_NAME",
    "SLACK_CONVERSATION_INSTRUCTIONS",
    "SLACK_MARKDOWN_INSTRUCTIONS",
    "create_channel_response_format_middleware",
]
