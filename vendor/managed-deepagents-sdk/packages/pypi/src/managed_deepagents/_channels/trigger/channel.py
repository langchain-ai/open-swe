"""Trigger Server-backed Slack channel authoring surface."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from managed_deepagents._channel import Channel
from managed_deepagents._channels.reactions import (
    DEFAULT_REACTION_EMOJI,
    ReactionChoice,
    is_emoji_name,
)

if TYPE_CHECKING:
    from managed_deepagents._channels.manifest import ChannelManifest, JsonObject

_SLACK_APP_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\- ]+$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class TriggerSlackAppConfig(TypedDict, total=False):
    """Slack app display fields provisioned by Trigger Server."""

    name: str
    description: str
    icon: str
    backgroundColor: str


class TriggerSlackChannel(Channel):
    """Slack events and deploy requirements routed through Trigger Server."""

    kind = "slack"
    transport: Literal["trigger_server"] = "trigger_server"

    def __init__(
        self,
        *,
        app: TriggerSlackAppConfig,
        trigger_on_all_messages: bool,
        allow_bot_triggers: bool,
        reactions: ReactionChoice = DEFAULT_REACTION_EMOJI,
    ) -> None:
        self.app = app
        self.trigger_on_all_messages = trigger_on_all_messages
        self.allow_bot_triggers = allow_bot_triggers
        self.reactions = reactions

    def events(self, ctx: object) -> object:
        from managed_deepagents._channels.slack.message_content import (  # noqa: PLC0415
            with_slack_rich_content,
        )
        from managed_deepagents._channels.trigger.http import (  # noqa: PLC0415
            handle_trigger_channel_event_request,
        )

        context = ctx if isinstance(ctx, dict) else {}
        return handle_trigger_channel_event_request(
            context["request"],
            raw_body=context.get("raw_body"),
            source_provider="slack",
            channel_name=context["name"],
            reactions=self.reactions,
            message_input=lambda event: with_slack_rich_content(
                event["input"]["messages"], event.get("raw_event")
            ),
        )

    def requirements(self, name: str) -> ChannelManifest:
        return {
            "name": name,
            "provider": "slack",
            "transport": self.transport,
            "autoReply": True,
            "requiredPermissions": [],
            "requiredEnv": [],
            "providerConfig": {
                "app": cast("JsonObject", self.app),
                "triggerOnAllMessages": self.trigger_on_all_messages,
                "allowBotTriggers": self.allow_bot_triggers,
            },
        }


class _TriggerSlackArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=35)
    description: str | None = Field(default=None, max_length=139)
    icon: str | None = Field(default=None, min_length=1)
    background_color: str | None = None
    trigger_on_all_messages: StrictBool = False
    allow_bot_triggers: StrictBool = False
    reactions: StrictBool | str | Callable[..., object] = DEFAULT_REACTION_EMOJI

    @field_validator("reactions")
    @classmethod
    def _validate_reactions(cls, value: object) -> object:
        if value is True:
            return DEFAULT_REACTION_EMOJI
        if value is False or callable(value):
            return value
        if is_emoji_name(value):
            return value
        raise ValueError("must be a bool, a Slack emoji short name, or a callable returning one")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if _SLACK_APP_NAME_RE.fullmatch(value) is None or value.strip(" -") != value:
            raise ValueError(
                "name can only contain letters, numbers, spaces, underscores, "
                "dashes, and periods, and cannot start or end with a space or dash"
            )
        return value

    @field_validator("background_color")
    @classmethod
    def validate_background_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _HEX_COLOR_RE.fullmatch(value) is None:
            raise ValueError("background_color must be a #RRGGBB hex color")
        return value.lower()


def slack_channel(
    *,
    name: str | None = None,
    description: str | None = None,
    icon: str | None = None,
    background_color: str | None = None,
    trigger_on_all_messages: bool = False,
    allow_bot_triggers: bool = False,
    reactions: ReactionChoice = DEFAULT_REACTION_EMOJI,
) -> TriggerSlackChannel:
    """Declare a Trigger Server-backed Slack channel.

    Args:
        name: Your agent's name, used in mentions like ``@support-agent``.
        description: A short description of what your agent does.
        icon: Optional path to a custom Slack app icon under ``channels/``.
        background_color: Optional Slack app background as a ``#RRGGBB`` hex color.
        reactions: Emoji to react with when a message starts a run; ``eyes`` by
            default, and ``True`` keeps it. Pass ``False`` for none, another
            Slack emoji short name, or a callable that picks one per message.
            Return ``None`` to skip a reaction.
    """
    try:
        parsed = _TriggerSlackArgs.model_validate(
            {
                "name": name,
                "description": description,
                "icon": icon,
                "background_color": background_color,
                "trigger_on_all_messages": trigger_on_all_messages,
                "allow_bot_triggers": allow_bot_triggers,
                "reactions": reactions,
            }
        )
    except ValueError as error:
        raise ValueError(f"channels.slack(...) received invalid configuration:\n{error}") from error
    app: TriggerSlackAppConfig = {}
    if parsed.name is not None:
        app["name"] = parsed.name
    if parsed.description is not None:
        app["description"] = parsed.description
    if parsed.icon is not None:
        app["icon"] = parsed.icon
    if parsed.background_color is not None:
        app["backgroundColor"] = parsed.background_color
    return TriggerSlackChannel(
        app=app,
        trigger_on_all_messages=parsed.trigger_on_all_messages,
        allow_bot_triggers=parsed.allow_bot_triggers,
        reactions=cast("ReactionChoice", parsed.reactions),
    )


__all__ = [
    "TriggerSlackAppConfig",
    "TriggerSlackChannel",
    "slack_channel",
]
