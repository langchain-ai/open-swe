"""Trigger Server-backed Slack channel authoring surface."""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable, Mapping
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
    from managed_deepagents._channels.slack.events import SlackEventCallback, SlackHandlers
    from managed_deepagents._channels.trigger.http import (
        TriggerChannelThread,
        TriggerMessageDecision,
    )
    from managed_deepagents._channels.trigger.types import TriggerChannelMessageEvent
    from managed_deepagents._channels.types import JsonValue
    from managed_deepagents._ingress_runs import MultitaskStrategy

_SLACK_APP_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\- ]+$")

SlackTriggerMode = Literal["mention", "thread", "all"]
"""Which channel messages start a run. DMs never need a mention.

- ``"mention"``: only messages that @-mention your agent, including replies in
  a thread it already answered.
- ``"thread"``: a message that @-mentions your agent, and every later reply in
  that thread, so people only mention it once. A thread whose mention your
  ``on_event`` ``"mention"`` handler declined stays declined: its replies
  start nothing.
- ``"all"``: every message in the channels your agent is in, mentioned or not.
"""

_UNREACHABLE_MESSAGE_HANDLER = (
    'channels.slack(on_event={"message": ...}) is never called with trigger_mode="mention", '
    'which only delivers mentions and direct messages; use trigger_mode "thread" or "all".'
)
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
        on_event: SlackHandlers | None = None,
        multitask_strategy: MultitaskStrategy = "enqueue",
    ) -> None:
        self.app = app
        self.trigger_mode = trigger_mode
        self.allow_bot_triggers = allow_bot_triggers
        self.reactions = reactions
        self.on_event = on_event
        self.multitask_strategy = multitask_strategy

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
        on_event = self.on_event
        name = context["name"]

        async def decide_message(
            event: TriggerChannelMessageEvent, thread: TriggerChannelThread
        ) -> TriggerMessageDecision | None:
            assert on_event is not None  # noqa: S101 - only passed with handlers
            return await _decide_message(on_event, event, thread, name)

        return handle_trigger_channel_event_request(
            context["request"],
            raw_body=context.get("raw_body"),
            source_provider="slack",
            channel_name=name,
            reactions=self.reactions,
            message_input=lambda event: with_slack_rich_content(
                event["input"]["messages"], event.get("raw_event")
            ),
            # A mention handler can decline a thread's mention, and Trigger
            # Server follows the thread anyway: its unmentioned replies only
            # continue a thread the agent already ran on.
            only_continues_thread=(
                _is_plain_message
                if on_event and "mention" in on_event and self.trigger_mode == "thread"
                else None
            ),
            decide_message=decide_message if on_event else None,
            multitask_strategy=self.multitask_strategy,
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


def _warn_unreachable_handlers(
    trigger_mode: SlackTriggerMode | None, on_event: SlackHandlers | None
) -> None:
    """Warn of a ``"message"`` handler that ``"mention"`` mode never delivers to."""
    if trigger_mode == "mention" and on_event and on_event.get("message") is not None:
        warnings.warn(_UNREACHABLE_MESSAGE_HANDLER, UserWarning, stacklevel=3)


async def _is_plain_message(event: TriggerChannelMessageEvent) -> bool:
    """Whether a delivery is surely neither a mention nor a direct message."""
    from managed_deepagents._channels.slack.events import (  # noqa: PLC0415
        is_plain_slack_message,
    )

    raw_event = event.get("raw_event")
    return isinstance(raw_event, Mapping) and await is_plain_slack_message(raw_event)


async def _decide_message(
    on_event: SlackHandlers,
    delivery: TriggerChannelMessageEvent,
    thread: TriggerChannelThread,
    channel_name: str,
) -> TriggerMessageDecision | None:
    """What a message starts, as its kind's handler decides.

    ``None``, so the message itself, without one. The Slack message's blocks
    and attachments are added to the handler's messages, as to a message's own.
    """
    from managed_deepagents._channels.http.provider import run_event_handler  # noqa: PLC0415
    from managed_deepagents._channels.run_input import normalize_run_input  # noqa: PLC0415
    from managed_deepagents._channels.slack.events import (  # noqa: PLC0415
        read_slack_event,
        slack_thread,
    )
    from managed_deepagents._channels.slack.message_content import (  # noqa: PLC0415
        with_slack_rich_content,
    )
    from managed_deepagents._channels.trigger.http import (  # noqa: PLC0415
        TriggerMessageDecision,
    )
    from managed_deepagents._log import logger  # noqa: PLC0415

    event = await read_slack_event(delivery)
    handler = cast("SlackEventCallback | None", on_event.get(event.key)) if event else None
    if event is None or handler is None:
        return None
    outcome = await run_event_handler(
        "Slack",
        event.key,
        event.delivery_id,
        lambda: handler(event, slack_thread(thread, channel_name)),
    )
    if outcome.type != "input":
        return TriggerMessageDecision(outcome.type)
    try:
        run_input = normalize_run_input(outcome.input)
    except TypeError as error:
        logger.error(
            "slack handler returned invalid run input (%s)", logger.safe_error_label(error)
        )
        return TriggerMessageDecision("failed")
    if run_input is None:
        return TriggerMessageDecision("skipped")
    if run_input["messages"]:
        run_input["messages"] = cast(
            "list[dict[str, JsonValue]]",
            with_slack_rich_content(
                cast("list[dict[str, object]]", run_input["messages"]), event.payload
            ),
        )
    return TriggerMessageDecision("input", run_input)


def _check_on_event(on_event: Mapping[str, object] | None) -> None:
    """Check ``on_event``'s keys and handlers; a key nothing dispatches never runs."""
    if on_event is None:
        return
    if not isinstance(on_event, Mapping):
        raise TypeError("channels.slack(...) on_event must be a mapping of handlers")
    from managed_deepagents._channels.http.provider import check_event_keys  # noqa: PLC0415
    from managed_deepagents._channels.slack.events import SLACK_EVENT_KEYS  # noqa: PLC0415

    check_event_keys("Slack", on_event, SLACK_EVENT_KEYS)
    for key, handler in on_event.items():
        if handler is not None and not callable(handler):
            raise TypeError(f'channels.slack(...) on_event["{key}"] must be callable')


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
    on_event: SlackHandlers | None = None,
    multitask_strategy: MultitaskStrategy = "enqueue",
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
        on_event: Handlers that decide what each kind of message starts, by
            key: ``"mention"`` (a channel message that @-mentions your agent),
            ``"directMessage"``, and ``"message"`` (any other channel message,
            which only ``trigger_mode`` ``"thread"`` or ``"all"`` delivers). A
            handler gets the ``SlackEvent`` and its ``SlackChannelThread``, and
            returns a prompt (string, message, or state), optionally with run
            options as ``(input, {"context", "metadata", "webhook"})``, or
            ``None`` to start no run: then the agent doesn't react or reply. A
            message kind without a handler starts a run with the message, as
            without ``on_event``. The message's blocks, attachments and files
            still reach the run.
        multitask_strategy: What a message that starts a run does while its
            thread already runs: ``enqueue`` (the default) waits for the run
            and starts after it, ``interrupt`` cancels the run, keeping its
            progress, and starts at once, ``rollback`` cancels it and discards
            its progress, and ``reject`` starts nothing.

    Example::

        channels.slack(
            name="Support Bot",
            on_event={
                "mention": lambda e, _: (e.text, {"metadata": {"slack_channel": e.channel_id}}),
                "message": lambda e, _: None,
            },
        )
    """
    _check_on_event(on_event)
    from managed_deepagents._ingress_runs import MULTITASK_STRATEGIES  # noqa: PLC0415

    if multitask_strategy not in MULTITASK_STRATEGIES:
        raise ValueError(
            "channels.slack(...) multitask_strategy must be enqueue, interrupt, rollback or reject"
        )
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
    _warn_unreachable_handlers(parsed.trigger_mode, on_event)
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
        on_event=on_event,
        multitask_strategy=multitask_strategy,
    )


__all__ = [
    "SlackTriggerMode",
    "TriggerSlackAppConfig",
    "TriggerSlackChannel",
    "slack_channel",
]
