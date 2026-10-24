"""The Slack messages a channel's ``on_event`` handlers see.

Which kind of message each is, and the message as a handler reads it.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, NotRequired, cast

from typing_extensions import TypedDict

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
    from managed_deepagents._channels.http.types import ChannelName
    from managed_deepagents._channels.run_input import ChannelRunResult
    from managed_deepagents._channels.trigger.http import TriggerChannelThread
    from managed_deepagents._channels.trigger.types import TriggerChannelMessageEvent

SlackEventKey = Literal["mention", "directMessage", "message"]
"""Which kind of Slack message reached the agent.

- ``"mention"``: a channel message that @-mentions your agent.
- ``"directMessage"``: a direct message to your agent.
- ``"message"``: any other channel message, which only ``trigger_mode``
  ``"thread"`` or ``"all"`` delivers.
"""

SLACK_EVENT_KEYS: frozenset[str] = frozenset({"mention", "directMessage", "message"})


@dataclass(frozen=True)
class SlackFile:
    """A file attached to a Slack message, as Slack describes it."""

    id: str
    name: str | None = None
    mimetype: str | None = None
    size: int | None = None
    """Size in bytes."""


@dataclass(frozen=True, kw_only=True)
class SlackEvent:
    """A Slack message that reached the agent.

    The run it starts can read its files under ``/workspace/attachments``, as
    without a handler.
    """

    key: SlackEventKey
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
    """The Slack user who sent it; ``None`` for some bot messages."""
    files: tuple[SlackFile, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SlackChannelThread:
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


SlackEventCallback = Callable[
    [SlackEvent, SlackChannelThread],
    "ChannelRunResult | Awaitable[ChannelRunResult | None] | None",
]
"""A handler: returns a run's input, as ``agent.invoke`` takes it, optionally
with run options as ``(input, {"context", "metadata", "webhook"})``, or ``None``
to start nothing."""


class SlackHandlers(TypedDict, total=False):
    """Handlers that decide what each kind of message starts, by its key.

    A handler may also be an endpoint, a URL or an ``httpx.Request`` carrying
    headers: MDA answers Trigger Server, then POSTs it the message and its
    thread, and starts the run it answers with.
    """

    mention: SlackEventCallback | EventEndpoint
    directMessage: SlackEventCallback | EventEndpoint
    message: SlackEventCallback | EventEndpoint


class SlackEndpointFile(TypedDict):
    """A ``SlackFile`` as an endpoint is sent it."""

    id: str
    name: NotRequired[str]
    mimetype: NotRequired[str]
    size: NotRequired[int]


class SlackEndpointFields(TypedDict, total=False):
    """A Slack event's fields its endpoints are sent beside its key, delivery ID
    and payload, as a handler gets them on ``SlackEvent``.

    Each is optional, so a receiver takes requests from deployments that send
    fewer. ``userId`` is absent for some bot messages.
    """

    text: str
    userId: str
    channelId: str
    messageTs: str
    threadTs: str
    files: list[SlackEndpointFile]


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
    fields: SlackEndpointFields = {
        "text": event.text,
        "channelId": event.channel_id,
        "messageTs": event.message_ts,
        "threadTs": event.thread_ts,
        "files": [_endpoint_file(file) for file in event.files],
    }
    if event.user_id is not None:
        fields["userId"] = event.user_id
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


async def slack_event_key(raw_event: Mapping[str, object]) -> SlackEventKey:
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


def reset_slack_bot_user() -> None:
    """Forget the bot user's ID; for tests."""
    _bot_user.reset()


async def read_slack_event(event: TriggerChannelMessageEvent) -> SlackEvent | None:
    """The message a delivery carries, of the kind Slack's event says.

    ``None`` when the delivery has no Slack event.
    """
    raw = event.get("raw_event")
    if not isinstance(raw, Mapping):
        return None
    return slack_event(await slack_event_key(raw), event)


def slack_thread(thread: TriggerChannelThread, name: str) -> SlackChannelThread:
    """The Slack thread of a Trigger message's thread, for a channel named ``name``."""
    from managed_deepagents._channels.http.types import ChannelName  # noqa: PLC0415

    return SlackChannelThread(
        id=thread["id"],
        source_thread_key=thread["source_thread_key"],
        channel=ChannelName(name=name),
    )


def slack_event(key: SlackEventKey, event: TriggerChannelMessageEvent) -> SlackEvent | None:
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
    return SlackEvent(
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
    "endpoint_fields",
    "read_slack_event",
    "reset_slack_bot_user",
    "slack_event",
    "slack_event_key",
    "slack_thread",
]
