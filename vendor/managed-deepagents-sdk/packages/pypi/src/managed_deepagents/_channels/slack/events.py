"""The Slack messages a channel's ``on_event`` handlers see.

Which kind of message each is, and the message as a handler reads it.
"""

from __future__ import annotations

import copy
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypeAlias, cast

from typing_extensions import TypedDict

from managed_deepagents._channels.actor import (
    ChannelActor,
    SlackActor,
    SlackEndpointActor,
    endpoint_actor,
)
from managed_deepagents._channels.channel_types import (
    ChannelEventCallback,
    ReadsThreadState,
)
from managed_deepagents._channels.http.endpoint import (
    EndpointEvent,
    EndpointRequest,
    EventEndpoint,
)
from managed_deepagents._channels.http.provider import CachedRead
from managed_deepagents._channels.types import (
    JsonValue,
)
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from langgraph_sdk.schema import ThreadState

    from managed_deepagents._channels.http.types import ChannelName, HttpFinishedDelivery
    from managed_deepagents._channels.trigger.http import TriggerChannelThread
    from managed_deepagents._channels.trigger.types import (
        TriggerChannelMessageEvent,
        TriggerChannelProviderEvent,
        TriggerChannelUserPromptResponseEvent,
    )

SlackMessageKey = Literal["mention", "directMessage", "message"]
"""Which kind of Slack message reached the agent.

- ``"mention"``: a channel message that @-mentions your agent.
- ``"directMessage"``: a direct message to your agent.
- ``"message"``: any other channel message, which only ``trigger_mode``
  ``"thread"`` or ``"all"`` delivers.
"""

SlackActivityKey = Literal["edit", "delete", "reaction", "shortcut"]
"""What happened to a Slack message: ``"edit"``, ``"delete"``, ``"reaction"``,
an emoji added or removed, or ``"shortcut"``, one of your app's message
shortcuts a person used on it."""

SlackEventKey = Literal[
    "mention",
    "directMessage",
    "message",
    "interaction",
    "edit",
    "delete",
    "reaction",
    "shortcut",
    "channel",
]
"""What reached the agent from Slack: a kind of message (``SlackMessageKey``),
``"interaction"``, a click on a button in a message your agent posted with its
own blocks, or a submitted modal it opened, that answers no decision the thread
waits on, something that happened to a message (``SlackActivityKey``), or
``"channel"``, a change to a channel itself."""

SLACK_EVENT_KEYS: frozenset[str] = frozenset(
    {
        "mention",
        "directMessage",
        "message",
        "interaction",
        "edit",
        "delete",
        "reaction",
        "shortcut",
        "channel",
    }
)

#: The Slack events Trigger Server forwards for each activity key and for
#: ``channel``: a channel asks for them only when it has a handler for the key.
SLACK_FORWARDED_EVENTS: dict[str, tuple[str, ...]] = {
    "edit": ("message_changed",),
    "delete": ("message_deleted",),
    "reaction": ("reaction_added", "reaction_removed"),
    "shortcut": ("message_action",),
    # ``group_*`` are a private channel's.
    "channel": (
        "channel_created",
        "channel_rename",
        "channel_archive",
        "channel_unarchive",
        "channel_deleted",
        "group_rename",
        "group_archive",
        "group_unarchive",
        "group_deleted",
    ),
}
_ACTIVITY_KEYS: dict[str, SlackActivityKey] = {
    "message_changed": "edit",
    "message_deleted": "delete",
    "reaction_added": "reaction",
    "reaction_removed": "reaction",
    "message_action": "shortcut",
}


@dataclass(frozen=True)
class SlackFile:
    """A file attached to a Slack message, as Slack describes it."""

    id: str
    name: str | None = None
    mimetype: str | None = None
    size: int | None = None
    """Size in bytes."""


@dataclass(frozen=True, kw_only=True)
class SlackMessageEvent:
    """A Slack message that reached the agent.

    The run it starts can read its files under ``/workspace/attachments``, as
    without a handler.
    """

    key: SlackMessageKey
    delivery_id: str
    """Trigger Server's ID for this delivery."""
    text: str
    """The message's text as Slack sent it, with mentions as ``<@U123>``."""
    channel_id: str
    """The Slack conversation: a channel's or a direct message's ID."""
    message_ts: str
    """The message's timestamp, its ID in Slack."""
    thread_ts: str
    """The timestamp of its thread's first message; ``message_ts`` when it starts one."""
    payload: dict[str, JsonValue]
    """The Slack event as Slack sent it, from the Events API."""
    user_id: str | None = None
    """The Slack user who sent it, as Slack's event says; ``None`` for some bot messages."""
    files: tuple[SlackFile, ...] = field(default_factory=tuple)
    actor: SlackActor | None = None
    """The Slack user who sent the message, as Trigger Server verified it from
    Slack's signed request. Its ``team_id`` is their own workspace, which in a
    Slack Connect channel isn't your app's. ``None`` when Trigger Server names
    no one, such as an older Trigger Server or a message from a bot. It is
    personal data: MDA neither logs it nor adds it to the run's metadata."""
    team_id: str | None = None
    """The Slack workspace your app is installed in, as Trigger Server keyed
    the conversation. In a Slack Connect channel the sender's own workspace is
    ``actor.team_id``."""


@dataclass(frozen=True, kw_only=True)
class SlackInteractionEvent:
    """A click on a button in a message your agent posted with its own blocks.

    Or a submitted modal it opened, as Trigger Server forwards it: one that
    answers no decision the thread waits on. A click on a decision's buttons
    resumes the run instead.
    """

    key: Literal["interaction"] = "interaction"
    delivery_id: str
    """Trigger Server's ID for this delivery."""
    action_id: str
    """The button's ``action_id``, or the modal's ``callback_id``."""
    channel_id: str
    """The Slack conversation the message is in."""
    thread_ts: str
    """The timestamp of its thread's first message."""
    payload: dict[str, JsonValue]
    """Slack's interaction payload, such as a ``block_actions`` one, with its
    ``response_url`` and ``actions``, without its verification ``token``."""
    value: JsonValue = None
    """The button's ``value``, or the modal's ``state.values``."""
    message_ts: str | None = None
    """The timestamp of the message holding the button; ``None`` for a modal."""
    user_id: str | None = None
    """The Slack user who clicked, as Slack's payload says."""
    actor: SlackActor | None = None
    """The Slack user who clicked, as Trigger Server verified it; see
    ``SlackMessageEvent.actor``."""


@dataclass(frozen=True, kw_only=True)
class SlackActivityEvent:
    """Something that happened to a Slack message: an edit, a deletion, a
    reaction, or a message shortcut a person used on it.

    Trigger Server forwards an edit, a deletion or a reaction only for a thread
    the channel already took a message from, unless the channel takes every
    message (``trigger_mode="all"``) or it is in a direct message, and never for
    your agent's own. A shortcut goes wherever a person used it, as a mention
    does; your Slack app defines its shortcuts and answers through the payload's
    ``response_url``.
    """

    key: SlackActivityKey
    delivery_id: str
    """Trigger Server's ID for this delivery."""
    channel_id: str
    """The Slack conversation the message is in."""
    message_ts: str
    """The timestamp of the message edited, deleted or reacted to."""
    thread_ts: str
    """The timestamp of its thread's first message."""
    text: str
    """The message's text: as edited, as it was before it was deleted, or as reacted to."""
    payload: dict[str, JsonValue]
    """The Slack event as Slack sent it, from the Events API, or a shortcut's
    interaction payload, without its verification ``token``."""
    user_id: str | None = None
    """The message's author for an edit or a deletion, the person who reacted
    for a reaction, the person who used it for a shortcut. On a deletion that
    may not be who deleted it, such as an admin: Slack doesn't say."""
    action_id: str | None = None
    """A shortcut's ``callback_id``."""
    reaction: str | None = None
    """The emoji's name, such as ``"thumbsup"``, for a reaction."""
    removed: bool = False
    """Whether a reaction was removed rather than added."""
    actor: SlackActor | None = None
    """Who acted, as Trigger Server verified it; see ``SlackMessageEvent.actor``."""


@dataclass(frozen=True, kw_only=True)
class SlackChannelEvent:
    """A change to a Slack channel itself: created, renamed, archived, unarchived
    or deleted. It names no message or thread, and starts no run unless the
    handler returns one."""

    key: Literal["channel"] = "channel"
    delivery_id: str
    """Trigger Server's ID for this delivery."""
    type: str
    """Slack's event type, such as ``"channel_archive"``; ``group_*`` for a private channel."""
    channel_id: str
    """The channel that changed."""
    team_id: str
    """The Slack workspace the channel is in."""
    payload: dict[str, JsonValue]
    """The Slack event as Slack sent it."""
    user_id: str | None = None
    """Who changed it, where Slack says."""
    actor: SlackActor | None = None
    """Who acted, as Trigger Server verified it; see ``SlackMessageEvent.actor``."""


SlackEvent: TypeAlias = (
    SlackMessageEvent | SlackInteractionEvent | SlackActivityEvent | SlackChannelEvent
)
"""What reached the agent from Slack, as a handler gets it: a message
(``SlackMessageEvent``), a click or modal (``SlackInteractionEvent``), an
edit, deletion or reaction (``SlackActivityEvent``), or a channel's own change
(``SlackChannelEvent``); switch on ``key``."""


@dataclass(frozen=True)
class SlackChannelThread(ReadsThreadState):
    """The conversation's thread a message belongs to.

    A handler runs before any run exists, so this is not a run's runtime.
    """

    id: str
    """The thread the run the handler starts runs on, one per Slack thread.
    Useful for run context that names the thread, such as a token a backend
    signs for it."""
    source_thread_key: str
    """The key the thread is derived from, which names the Slack thread."""
    channel: ChannelName
    _get_state: Callable[[], Awaitable[ThreadState | None]] | None = field(
        default=None, repr=False, compare=False
    )


SlackEventCallback = ChannelEventCallback[SlackMessageEvent, SlackChannelThread]
"""A handler: returns a run's input, as ``agent.invoke`` takes it, or a list of one
run with options, as ``[{"input", "context", "metadata", "webhook"}]``, ``None`` to
start nothing, or ``reply(message)`` to start nothing and post ``message`` in the
thread."""

SlackInteractionCallback = ChannelEventCallback[SlackInteractionEvent, SlackChannelThread]
"""An ``interaction`` handler, as a ``SlackEventCallback`` but for an interaction."""

SlackActivityCallback = ChannelEventCallback[SlackActivityEvent, SlackChannelThread]
"""An ``edit``, ``delete``, ``reaction`` or ``shortcut`` handler, as a
``SlackEventCallback`` but for something that happened to a message."""

SlackChannelCallback = ChannelEventCallback[SlackChannelEvent, SlackChannelThread]
"""A ``channel`` handler, as a ``SlackEventCallback`` but for a channel's own change."""


class SlackHandlers(TypedDict, total=False):
    """Handlers that decide what each kind of message starts, by its key.

    A handler may also be an endpoint, a URL or an ``httpx.Request`` carrying
    headers: MDA answers Trigger Server, then POSTs it the message and its
    thread, and starts the run it answers with.
    """

    mention: SlackEventCallback | EventEndpoint
    directMessage: SlackEventCallback | EventEndpoint
    message: SlackEventCallback | EventEndpoint
    interaction: SlackInteractionCallback | EventEndpoint
    """Decides what a click on a button your agent posted with its own blocks
    starts, such as the next prompt, or ``None`` after recording it. Without
    one, such a click starts a run with no new message, which reads the click
    from ``runtime.channel.event``. A click that answers a decision the thread
    waits on resumes the run, and never reaches it."""
    edit: SlackActivityCallback | EventEndpoint
    """Decides what a message's edit starts. With it, the channel asks Trigger
    Server for edits; without it, they never reach the deployment."""
    delete: SlackActivityCallback | EventEndpoint
    """Decides what a message's deletion starts, as ``edit``."""
    reaction: SlackActivityCallback | EventEndpoint
    """Decides what an emoji added to or removed from a message starts, as
    ``edit``. Your app's Slack app then needs ``reactions:read``, which a
    LangSmith-managed app gets at its next deploy, after a reinstall."""
    shortcut: SlackActivityCallback | EventEndpoint
    """Decides what one of your app's message shortcuts starts, used on a
    message, as ``edit``. Your Slack app defines its shortcuts and needs
    ``commands``."""
    channel: SlackChannelCallback | EventEndpoint
    """Decides what a channel's creation, rename, archive, unarchive or deletion
    starts, as ``edit``."""


class SlackEndpointFile(TypedDict):
    """A ``SlackFile`` as an endpoint is sent it."""

    id: str
    name: NotRequired[str]
    mimetype: NotRequired[str]
    size: NotRequired[int]


class SlackEndpointFields(TypedDict, total=False):
    """A Slack event's fields its endpoints are sent beside its key, delivery ID
    and payload, as a handler gets them on ``SlackMessageEvent`` or
    ``SlackInteractionEvent``: those its kind has.

    Each is optional, so a receiver takes requests from deployments that send
    fewer. ``userId`` is absent for some bot messages.
    """

    text: str
    userId: str
    channelId: str
    messageTs: str
    threadTs: str
    files: list[SlackEndpointFile]
    actor: SlackEndpointActor
    """The verified Slack user; absent when Trigger Server signs none."""
    actionId: str
    """An interaction's button ``action_id`` or modal ``callback_id``, or a
    shortcut's ``callback_id``."""
    reaction: str
    """A reaction's emoji name."""
    removed: bool
    """Whether a reaction was removed."""
    value: JsonValue
    """An interaction's button ``value`` or modal ``state.values``."""
    teamId: str
    """The Slack workspace your app is installed in, for a message or a channel's own change."""


class SlackEndpointEvent(EndpointEvent[SlackEventKey, dict[str, JsonValue]], SlackEndpointFields):
    """The event a Slack channel sends its endpoint: its key, delivery ID and
    Slack's own event as ``payload``, with the fields of ``SlackEndpointFields``."""


SlackEndpointRequest = EndpointRequest[SlackEndpointEvent]
"""What a Slack channel POSTs an endpoint; validate it with
``TypeAdapter(SlackEndpointRequest)``."""


def endpoint_fields(event: SlackEvent) -> SlackEndpointFields:
    """The fields of a Slack event its endpoints are sent beside key, delivery ID
    and payload: the one place they're listed, as TypeScript's
    ``SLACK_ENDPOINT_EVENT_FIELDS``."""
    if isinstance(event, SlackInteractionEvent):
        return _interaction_endpoint_fields(event)
    if isinstance(event, SlackActivityEvent):
        return _activity_endpoint_fields(event)
    if isinstance(event, SlackChannelEvent):
        changed: SlackEndpointFields = {"channelId": event.channel_id, "teamId": event.team_id}
        if event.user_id is not None:
            changed["userId"] = event.user_id
        if event.actor is not None:
            changed["actor"] = endpoint_actor(event.actor)
        return changed
    fields: SlackEndpointFields = {
        "text": event.text,
        "channelId": event.channel_id,
        "messageTs": event.message_ts,
        "threadTs": event.thread_ts,
        "files": [_endpoint_file(file) for file in event.files],
    }
    if event.user_id is not None:
        fields["userId"] = event.user_id
    if event.actor is not None:
        fields["actor"] = endpoint_actor(event.actor)
    if event.team_id is not None:
        fields["teamId"] = event.team_id
    return fields


def _activity_endpoint_fields(event: SlackActivityEvent) -> SlackEndpointFields:
    fields: SlackEndpointFields = {
        "text": event.text,
        "channelId": event.channel_id,
        "messageTs": event.message_ts,
        "threadTs": event.thread_ts,
    }
    if event.user_id is not None:
        fields["userId"] = event.user_id
    if event.reaction is not None:
        fields["reaction"] = event.reaction
        fields["removed"] = event.removed
    if event.action_id is not None:
        fields["actionId"] = event.action_id
    if event.actor is not None:
        fields["actor"] = endpoint_actor(event.actor)
    return fields


def _interaction_endpoint_fields(event: SlackInteractionEvent) -> SlackEndpointFields:
    fields: SlackEndpointFields = {
        "actionId": event.action_id,
        "channelId": event.channel_id,
        "threadTs": event.thread_ts,
    }
    if event.value is not None:
        fields["value"] = event.value
    if event.message_ts is not None:
        fields["messageTs"] = event.message_ts
    if event.user_id is not None:
        fields["userId"] = event.user_id
    if event.actor is not None:
        fields["actor"] = endpoint_actor(event.actor)
    return fields


def _endpoint_file(file: SlackFile) -> SlackEndpointFile:
    sent: SlackEndpointFile = {"id": file.id}
    if file.name is not None:
        sent["name"] = file.name
    if file.mimetype is not None:
        sent["mimetype"] = file.mimetype
    if file.size is not None:
        sent["size"] = file.size
    return sent


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


async def slack_event_key(raw_event: Mapping[str, object]) -> SlackMessageKey:
    """Which kind of message a delivery is.

    Slack sends a mention as ``app_mention`` under ``trigger_mode`` ``"mention"``,
    and as a ``message`` that names the app's bot user under ``"thread"`` and
    ``"all"``, so only a channel ``message`` asks who the bot is.
    """
    if raw_event.get("type") == "app_mention":
        return "mention"
    if raw_event.get("channel_type") == "im":
        return "directMessage"
    body = _text(raw_event.get("text"))
    if body is None or "<@" not in body:
        return "message"
    bot_user_id = await _bot_user.get()
    if bot_user_id and (f"<@{bot_user_id}>" in body or f"<@{bot_user_id}|" in body):
        return "mention"
    return "message"


async def is_plain_slack_message(raw_event: Mapping[str, object]) -> bool:
    """Whether a delivery is surely a plain channel message, neither a mention
    nor a direct message. One that names a user while the bot user is unknown
    may be a mention, so it isn't."""
    if raw_event.get("type") == "app_mention" or raw_event.get("channel_type") == "im":
        return False
    body = _text(raw_event.get("text"))
    if body is None or "<@" not in body:
        return True
    bot_user_id = await _bot_user.get()
    return bool(bot_user_id) and not (f"<@{bot_user_id}>" in body or f"<@{bot_user_id}|" in body)


_BOT_USER_TTL_SECONDS = 3600.0
"""How long the bot user's ID is reused; an app keeps it for good."""
_MISSING_BOT_USER_TTL_SECONDS = 60.0
"""How long a failed lookup is remembered, so each message doesn't ask Slack."""


async def _read_bot_user_id() -> str | None:
    """The app's bot user, from Slack's ``auth.test`` with its bot token."""
    from managed_deepagents._channels.slack.api import (  # noqa: PLC0415
        slack_api,
        slack_bot_token,
    )

    try:
        identity = await slack_api(await slack_bot_token(), "auth.test", {})
    except Exception as error:  # noqa: BLE001 - credential errors can contain secrets
        logger.warning(
            "slack bot user lookup failed; mentions in channel messages count as messages (%s)",
            logger.safe_error_label(error),
        )
        return None
    return _text(identity.get("user_id"))


_bot_user: CachedRead[str | None] = CachedRead(
    _read_bot_user_id,
    lambda user_id: _BOT_USER_TTL_SECONDS if user_id else _MISSING_BOT_USER_TTL_SECONDS,
)


async def slack_bot_user_id() -> str | None:
    """The app's bot user, or ``None`` when Slack can't say."""
    return await _bot_user.get()


def reset_slack_bot_user() -> None:
    """Forget the bot user's ID; for tests."""
    _bot_user.reset()


async def read_slack_event(
    event: TriggerChannelMessageEvent
    | TriggerChannelUserPromptResponseEvent
    | TriggerChannelProviderEvent,
    actor: ChannelActor | None = None,
) -> SlackEvent | None:
    """What a delivery carries: a message, of the kind Slack's event says, an
    interaction, or an edit, deletion or reaction.

    ``None`` when it has nothing to read one from.
    """
    if event.get("type") == "user_prompt_response":
        return slack_interaction_event(cast("TriggerChannelUserPromptResponseEvent", event), actor)
    if event.get("type") == "provider_event":
        provider_event = cast("TriggerChannelProviderEvent", event)
        return slack_channel_event(provider_event, actor) or slack_activity_event(
            provider_event, actor
        )
    raw = event.get("raw_event")
    if not isinstance(raw, Mapping):
        return None
    message = cast("TriggerChannelMessageEvent", event)
    return slack_event(await slack_event_key(raw), message, actor)


def slack_thread(thread: TriggerChannelThread, name: str) -> SlackChannelThread:
    """The Slack thread of a Trigger message's thread, for a channel named ``name``."""
    from managed_deepagents._channels.http.types import ChannelName  # noqa: PLC0415

    return SlackChannelThread(
        id=thread["id"],
        source_thread_key=thread["source_thread_key"],
        channel=ChannelName(name=name),
        _get_state=thread.get("get_state"),
    )


def _key_team(source_thread_key: str) -> str | None:
    """The workspace in Trigger Server's verified key, ``slack:v1:<app>:<team>:…``."""
    match source_thread_key.split(":"):
        case ["slack", "v1", _, team, *_]:
            return _text(team)
        case _:
            return None


def _finished_delivery(
    raw_event: JsonValue | None,
) -> TriggerChannelMessageEvent | TriggerChannelUserPromptResponseEvent | None:
    """The Trigger delivery a finished message or interaction carries.

    See ``TriggerFinishedEvent``.
    """
    delivery = raw_event.get("delivery") if isinstance(raw_event, Mapping) else None
    return (
        cast("TriggerChannelMessageEvent | TriggerChannelUserPromptResponseEvent", delivery)
        if isinstance(delivery, Mapping)
        else None
    )


_MAX_DECIDED_EVENTS = 1024
"""How many deliveries' events a channel keeps until they finish."""


class DecidedSlackEvents:
    """The ``SlackEvent`` a handler got for each delivery, so ``on_finish`` gets
    that same event rather than reading the delivery again, which could read it
    as another kind. A delivery that never finishes, such as one Trigger Server
    sends again, is dropped once there are many."""

    def __init__(self) -> None:
        self._by_delivery: OrderedDict[str, SlackEvent] = OrderedDict()

    def remember(self, delivery_id: str, event: SlackEvent) -> None:
        self._by_delivery[delivery_id] = event
        self._by_delivery.move_to_end(delivery_id)
        if len(self._by_delivery) > _MAX_DECIDED_EVENTS:
            self._by_delivery.popitem(last=False)

    async def finished(self, raw_event: JsonValue | None) -> SlackEvent | None:
        """What a finished delivery carried: as its handler got it, or read
        from the delivery, with its verified actor, when no handler did."""
        delivery = _finished_delivery(raw_event)
        if delivery is None:
            return None
        decided = self._by_delivery.pop(delivery["delivery_id"], None)
        if decided is not None:
            return decided
        actor = raw_event.get("actor") if isinstance(raw_event, Mapping) else None
        return await read_slack_event(
            delivery,
            SlackActor(**cast("dict[str, Any]", actor)) if isinstance(actor, Mapping) else None,
        )


def finished_slack_thread(delivery: HttpFinishedDelivery, name: str) -> SlackChannelThread:
    """The Slack thread of a finished delivery, for a channel named ``name``."""
    message = _finished_delivery(delivery.get("raw_event"))
    return slack_thread(
        {
            "id": delivery["thread_id"],
            "source_thread_key": message["source_thread_key"] if message is not None else "",
        },
        name,
    )


def slack_event(
    key: SlackMessageKey, event: TriggerChannelMessageEvent, actor: ChannelActor | None = None
) -> SlackMessageEvent | None:
    """The message a delivery carries, as a handler reads it.

    ``None`` when the delivery has no Slack event to read it from.
    """
    raw = event.get("raw_event")
    if not isinstance(raw, Mapping):
        return None
    address = event["address"] if isinstance(event.get("address"), Mapping) else {}
    message = event.get("message")
    message = message if isinstance(message, Mapping) else {}
    channel_id = _text(raw.get("channel")) or _text(address.get("conversation_id"))
    message_ts = _text(raw.get("ts")) or _text(message.get("id"))
    if channel_id is None or message_ts is None:
        return None
    raw_text = raw.get("text")
    message_text = message.get("text")
    files = raw.get("files")
    return SlackMessageEvent(
        key=key,
        delivery_id=event["delivery_id"],
        text=raw_text
        if isinstance(raw_text, str)
        else (message_text if isinstance(message_text, str) else ""),
        user_id=_text(raw.get("user")),
        channel_id=channel_id,
        message_ts=message_ts,
        thread_ts=_text(raw.get("thread_ts")) or _text(address.get("thread_id")) or message_ts,
        files=tuple(
            slack_file
            for item in (files if isinstance(files, list) else [])
            if (slack_file := _slack_file(item)) is not None
        ),
        payload=cast("dict[str, JsonValue]", copy.deepcopy(dict(raw))),
        # A Slack message's actor is a Slack user.
        actor=actor if isinstance(actor, SlackActor) else None,
        team_id=_key_team(event["source_thread_key"]),
    )


def slack_channel_event(
    event: TriggerChannelProviderEvent, actor: ChannelActor | None = None
) -> SlackChannelEvent | None:
    """A forwarded change to a channel itself; ``None`` for any other event."""
    kind = event["provider_event"]["type"]
    address = event["address"] if isinstance(event.get("address"), Mapping) else {}
    channel_id = _text(address.get("conversation_id"))
    team_id = _key_team(event["source_thread_key"])
    if kind not in SLACK_FORWARDED_EVENTS["channel"] or channel_id is None or team_id is None:
        return None
    raw = event["raw_event"]
    channel = raw.get("channel")
    return SlackChannelEvent(
        delivery_id=event["delivery_id"],
        type=kind,
        channel_id=channel_id,
        team_id=team_id,
        payload=cast("dict[str, JsonValue]", copy.deepcopy(dict(raw))),
        user_id=_text(raw.get("user"))
        or (_text(channel.get("creator")) if isinstance(channel, Mapping) else None),
        actor=actor if isinstance(actor, SlackActor) else None,
    )


def slack_activity_event(
    event: TriggerChannelProviderEvent, actor: ChannelActor | None = None
) -> SlackActivityEvent | None:
    """The edit, deletion or reaction a forwarded provider event carries, as a
    handler reads it.

    ``None`` for one no key reads, without the message it is about, or without
    a reaction's emoji or a shortcut's ``callback_id``.
    """
    kind = event["provider_event"]
    key = _ACTIVITY_KEYS.get(kind.get("subtype") or kind["type"])
    raw = event["raw_event"]
    address = event["address"] if isinstance(event.get("address"), Mapping) else {}
    message = event.get("message")
    channel_id = _text(address.get("conversation_id"))
    message_ts = _text(message.get("id")) if message else None
    thread_ts = _text(address.get("thread_id")) or message_ts
    if key is None or channel_id is None or message_ts is None or thread_ts is None:
        return None
    reaction = _text(raw.get("reaction")) if key == "reaction" else None
    action_id = _text(raw.get("callback_id")) if key == "shortcut" else None
    if (key == "reaction" and reaction is None) or (key == "shortcut" and action_id is None):
        return None
    if key == "reaction":
        user_id = _text(raw.get("user"))
    elif key == "shortcut":
        user = raw.get("user")
        user_id = _text(user.get("id")) if isinstance(user, Mapping) else None
    else:
        changed = raw.get("message" if key == "edit" else "previous_message")
        user_id = _text(changed.get("user")) if isinstance(changed, Mapping) else None
    return SlackActivityEvent(
        key=key,
        delivery_id=event["delivery_id"],
        channel_id=channel_id,
        message_ts=message_ts,
        thread_ts=thread_ts,
        text=message["text"] if message else "",
        payload=cast("dict[str, JsonValue]", copy.deepcopy(dict(raw))),
        user_id=user_id,
        reaction=reaction,
        removed=kind["type"] == "reaction_removed",
        action_id=action_id,
        actor=actor if isinstance(actor, SlackActor) else None,
    )


def slack_interaction_event(
    event: TriggerChannelUserPromptResponseEvent, actor: ChannelActor | None = None
) -> SlackInteractionEvent | None:
    """The interaction a forwarded click or modal submission carries, as a handler reads it.

    ``None`` without the conversation it happened in.
    """
    raw = event.get("raw_event")
    raw = raw if isinstance(raw, Mapping) else {}
    address = event["address"] if isinstance(event.get("address"), Mapping) else {}
    container = raw.get("container")
    container = container if isinstance(container, Mapping) else {}
    message = raw.get("message")
    message = message if isinstance(message, Mapping) else {}
    channel = raw.get("channel")
    user = raw.get("user")
    message_ts = _text(container.get("message_ts")) or _text(message.get("ts"))
    channel_id = (
        _text(address.get("conversation_id"))
        or _text(container.get("channel_id"))
        or (_text(channel.get("id")) if isinstance(channel, Mapping) else None)
    )
    thread_ts = (
        _text(address.get("thread_id"))
        or _text(container.get("thread_ts"))
        or _text(message.get("thread_ts"))
        or message_ts
    )
    if channel_id is None or thread_ts is None:
        return None
    interaction = event["interaction"]
    return SlackInteractionEvent(
        delivery_id=event["delivery_id"],
        action_id=interaction["action"],
        value=cast("JsonValue", copy.deepcopy(interaction.get("value"))),
        user_id=_text(user.get("id")) if isinstance(user, Mapping) else None,
        channel_id=channel_id,
        message_ts=message_ts,
        thread_ts=thread_ts,
        # Slack's legacy verification token isn't the handler's to have.
        payload=cast(
            "dict[str, JsonValue]",
            copy.deepcopy({key: value for key, value in raw.items() if key != "token"}),
        ),
        # The actor of a click in Slack is a Slack user.
        actor=actor if isinstance(actor, SlackActor) else None,
    )


def _slack_file(item: object) -> SlackFile | None:
    if not isinstance(item, Mapping):
        return None
    file_id = _text(item.get("id"))
    if file_id is None:
        return None
    size = item.get("size")
    return SlackFile(
        id=file_id,
        name=_text(item.get("name")),
        mimetype=_text(item.get("mimetype")),
        size=size if isinstance(size, int) and not isinstance(size, bool) else None,
    )


__all__ = [
    "SLACK_EVENT_KEYS",
    "SLACK_FORWARDED_EVENTS",
    "DecidedSlackEvents",
    "SlackActivityCallback",
    "SlackActivityEvent",
    "SlackActivityKey",
    "SlackActor",
    "SlackChannelCallback",
    "SlackChannelEvent",
    "SlackChannelThread",
    "SlackEndpointEvent",
    "SlackEndpointFields",
    "SlackEndpointFile",
    "SlackEndpointRequest",
    "SlackEvent",
    "SlackEventCallback",
    "SlackEventKey",
    "SlackFile",
    "SlackHandlers",
    "SlackInteractionCallback",
    "SlackInteractionEvent",
    "SlackMessageEvent",
    "SlackMessageKey",
    "endpoint_fields",
    "finished_slack_thread",
    "read_slack_event",
    "reset_slack_bot_user",
    "slack_activity_event",
    "slack_channel_event",
    "slack_event",
    "slack_event_key",
    "slack_interaction_event",
    "slack_thread",
]
