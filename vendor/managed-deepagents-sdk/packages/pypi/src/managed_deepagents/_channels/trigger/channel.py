"""Trigger Server-backed Slack channel authoring surface."""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from managed_deepagents._channel import Channel
from managed_deepagents._channels.reactions import (
    DEFAULT_REACTION_EMOJI,
    ReactionChoice,
    is_emoji_name,
)

if TYPE_CHECKING:
    from managed_deepagents._channels.manifest import ChannelManifest, JsonObject

_SLACK_APP_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\- ]+$")

SlackTriggerMode = Literal["mention", "thread", "all"]
"""Which channel messages start a run. DMs never need a mention.

- ``"mention"``: only messages that @-mention your agent, including replies in
  a thread it already answered.
- ``"thread"``: a message that @-mentions your agent, and every later reply in
  that thread, so people only mention it once.
- ``"all"``: every message in the channels your agent is in, mentioned or not.
"""

_TRIGGER_ON_ALL_MESSAGES_DEPRECATION = (
    "channels.slack(trigger_on_all_messages=...) is deprecated; use trigger_mode instead "
    '(trigger_on_all_messages=True is trigger_mode="all").'
)
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
    transport: Literal["trigger_server"] = "trigger_server"  # ty: ignore[invalid-mutable-override]

    def __init__(
        self,
        *,
        app: TriggerSlackAppConfig,
        allow_bot_triggers: bool,
        trigger_mode: SlackTriggerMode | None = None,
        reactions: ReactionChoice = DEFAULT_REACTION_EMOJI,
    ) -> None:
        self.app = app
        self.trigger_mode = trigger_mode
        self.allow_bot_triggers = allow_bot_triggers
        self.reactions = reactions

    @property
    def trigger_on_all_messages(self) -> bool:
        """Deprecated: read ``trigger_mode``; ``True`` only for ``"all"``."""
        return self.trigger_mode == "all"

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
        # Older CLIs require triggerOnAllMessages and reject unknown fields, so
        # "all" is sent as that flag and triggerMode only for mention/thread.
        # Unset lets Trigger Server keep an existing app's mode or default a new
        # one to "mention".
        provider_config: JsonObject = {
            "app": cast("JsonObject", self.app),
            "triggerOnAllMessages": self.trigger_on_all_messages,
            "allowBotTriggers": self.allow_bot_triggers,
        }
        if self.trigger_mode in ("mention", "thread"):
            provider_config["triggerMode"] = self.trigger_mode
        return {
            "name": name,
            "provider": "slack",
            "transport": self.transport,
            "autoReply": True,
            "requiredPermissions": [],
            "requiredEnv": [],
            "providerConfig": provider_config,
        }


class _TriggerSlackArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=35)
    description: str | None = Field(default=None, max_length=139)
    icon: str | None = Field(default=None, min_length=1)
    background_color: str | None = None
    trigger_mode: SlackTriggerMode | None = None
    trigger_on_all_messages: StrictBool | None = None
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

    @model_validator(mode="after")
    def _reject_conflicting_trigger_modes(self) -> _TriggerSlackArgs:
        if (
            self.trigger_mode is not None
            and self.trigger_on_all_messages is not None
            and self.trigger_on_all_messages != (self.trigger_mode == "all")
        ):
            raise ValueError(
                "trigger_on_all_messages conflicts with trigger_mode; set only trigger_mode"
            )
        return self


def slack_channel(
    *,
    name: str | None = None,
    description: str | None = None,
    icon: str | None = None,
    background_color: str | None = None,
    trigger_mode: SlackTriggerMode | None = None,
    trigger_on_all_messages: bool | None = None,
    allow_bot_triggers: bool = False,
    reactions: ReactionChoice = DEFAULT_REACTION_EMOJI,
) -> TriggerSlackChannel:
    """Declare a Trigger Server-backed Slack channel.

    Args:
        name: Your agent's name, used in mentions like ``@support-agent``.
        description: A short description of what your agent does.
        icon: Optional path to a custom Slack app icon under ``channels/``.
        background_color: Optional Slack app background as a ``#RRGGBB`` hex color.
        trigger_mode: Which channel messages start a run; see
            ``SlackTriggerMode``. New Slack apps use ``"mention"``; set
            ``"thread"`` to keep replying in a thread after one mention. When
            unset, a deployed agent keeps its current mode, except that an
            all-messages agent that drops ``"all"`` moves to ``"mention"``.
            Redeploying with a new mode updates the existing Slack app.
        trigger_on_all_messages: Deprecated; use ``trigger_mode`` instead.
            ``True`` is ``trigger_mode="all"``. When both are set they must
            agree.
        allow_bot_triggers: Let messages from other bots start runs. In every
            trigger mode, a bot must still @-mention your agent explicitly in
            the message.
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
                "trigger_mode": trigger_mode,
                "trigger_on_all_messages": trigger_on_all_messages,
                "allow_bot_triggers": allow_bot_triggers,
                "reactions": reactions,
            }
        )
    except ValueError as error:
        raise ValueError(f"channels.slack(...) received invalid configuration:\n{error}") from error
    if trigger_on_all_messages is not None:
        warnings.warn(_TRIGGER_ON_ALL_MESSAGES_DEPRECATION, FutureWarning, stacklevel=2)
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
        trigger_mode=parsed.trigger_mode or ("all" if parsed.trigger_on_all_messages else None),
        allow_bot_triggers=parsed.allow_bot_triggers,
        reactions=cast("ReactionChoice", parsed.reactions),
    )


__all__ = [
    "SlackTriggerMode",
    "TriggerSlackAppConfig",
    "TriggerSlackChannel",
    "slack_channel",
]
