"""Managed ``schedules/`` authoring interface for the Python ecosystem.

A schedule is a proactive, time-triggered agent run. Files under
``schedules/*.py`` export a named ``schedule`` created with
:func:`define_schedule`; the compiler materializes those declarations into
LangGraph cron entries.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Literal, cast

from pydantic import ConfigDict, StringConstraints, with_config
from typing_extensions import TypedDict

from managed_deepagents._channels.types import SlackChannelTarget, normalize_channel_destination

if TYPE_CHECKING:
    from managed_deepagents._channels.types import ChannelTarget

JsonValue = object
_NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@with_config(ConfigDict(extra="forbid"))
class ScheduleThreadEphemeral(TypedDict, total=False):
    mode: Literal["ephemeral"]


@with_config(ConfigDict(extra="forbid"))
class ScheduleThreadPersistent(TypedDict):
    mode: Literal["persistent"]
    id: str


ThreadConfig = ScheduleThreadEphemeral | ScheduleThreadPersistent
"""A run's thread: ``{"mode": "ephemeral"}`` (the mode may be left out) for a
fresh thread, or ``{"mode": "persistent", "id": "<uuid>"}`` for the thread with
that id. A schedule's ``thread`` and a channel handler's run both take it."""

ScheduleThreadConfig = ThreadConfig
"""Deprecated: use ``ThreadConfig``."""


@with_config(ConfigDict(extra="forbid"))
class ChannelDestinationProviderThread(TypedDict):
    type: Literal["provider_thread"]
    conversation_id: _NonEmpty
    thread_id: _NonEmpty


@with_config(ConfigDict(extra="forbid"))
class ChannelDestinationProviderConversation(TypedDict):
    type: Literal["provider_conversation"]
    conversation_id: _NonEmpty


#: Schedules cannot use ``current_thread`` (no originating channel).
ScheduleChannelDestination = (
    ChannelDestinationProviderThread | ChannelDestinationProviderConversation
)


class ScheduleDeliveryTargetRequired(TypedDict):
    channel: _NonEmpty
    to: ScheduleChannelDestination


@with_config(ConfigDict(extra="forbid"))
class ScheduleDeliveryTarget(ScheduleDeliveryTargetRequired, total=False):
    """Where a schedule's run posts its answer, as MDA stores it.

    Deprecated as ``define_schedule``'s ``deliver_to``: use ``auto_post`` with a
    ``ChannelTarget``.
    """

    auto_post: bool


class ScheduleConfigRequired(TypedDict):
    cron: str


class ScheduleConfig(ScheduleConfigRequired, total=False):
    timezone: str
    prompt: str
    input: JsonValue
    thread: ThreadConfig
    deliver_to: ScheduleDeliveryTarget
    context: dict[str, JsonValue]


__all__ = [
    "ScheduleConfig",
    "ScheduleDefinition",
    "ScheduleDeliveryTarget",
    "ScheduleThreadConfig",
    "ThreadConfig",
    "define_schedule",
]

_CRON_FIELD_COUNT = 5


class ScheduleDefinition:
    """The pre-runtime spec returned by :func:`define_schedule`."""

    kind = "schedule"

    def __init__(self, config: ScheduleConfig) -> None:
        self.config = config

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"ScheduleDefinition(cron={self.config.get('cron')!r})"


def define_schedule(
    *,
    cron: str,
    timezone: str | None = None,
    prompt: str | None = None,
    input: JsonValue | None = None,
    thread: ThreadConfig | None = None,
    auto_post: Literal[False] | ChannelTarget | Mapping[str, object] | None = None,
    deliver_to: ScheduleDeliveryTarget | None = None,
    context: Mapping[str, JsonValue] | None = None,
) -> ScheduleDefinition:
    """Declare a managed schedule that MDA provisions as a LangGraph cron.

    ``auto_post`` is where the run posts its final answer: a ``ChannelTarget``,
    such as a ``SlackChannelTarget`` or, in a schedule file deploy reads, its literal
    ``{"provider": "slack", "conversation_id": ...}``, or ``False`` (the default) for nowhere. A
    schedule has no conversation to answer in, so ``True`` isn't taken. Only
    Slack targets, for now. ``deliver_to`` is its deprecated form.
    """

    if not isinstance(cron, str) or len(cron.strip().split()) != _CRON_FIELD_COUNT:
        raise ValueError(
            "define_schedule(...) requires `cron` to be a standard 5-field cron expression."
        )

    has_prompt = prompt is not None
    has_input = input is not None
    if has_prompt == has_input:
        raise ValueError("define_schedule(...) requires exactly one of `prompt` or `input`.")
    if prompt is not None and (not isinstance(prompt, str) or not prompt.strip()):
        raise ValueError("define_schedule(...) requires `prompt` to be a non-empty string.")

    _validate_thread(thread)
    if deliver_to is not None:
        warnings.warn(_DELIVER_TO_DEPRECATION, FutureWarning, stacklevel=2)
        if auto_post is not None:
            raise ValueError(_BOTH_DELIVERIES)
        _validate_deliver_to(deliver_to)
    elif auto_post is not None:
        deliver_to = _deliver_to_of(auto_post)
    if context is not None and not isinstance(context, Mapping):
        raise ValueError("define_schedule(...) requires `context` to be a mapping when provided.")

    optional = {
        "timezone": timezone,
        "prompt": prompt,
        "input": input,
        "thread": thread,
        "deliver_to": deliver_to,
        "context": None if context is None else dict(context),
    }
    config = cast(
        "ScheduleConfig",
        {"cron": cron, **{key: value for key, value in optional.items() if value is not None}},
    )
    return ScheduleDefinition(config)


def _validate_thread(thread: ThreadConfig | object | None) -> None:
    if thread is None:
        return
    if not isinstance(thread, dict):
        raise ValueError("define_schedule(...) requires `thread` to be a mapping.")
    mode = thread.get("mode", "ephemeral")
    if mode not in ("ephemeral", "persistent"):
        raise ValueError(
            'define_schedule(...) requires `thread["mode"]` to be "ephemeral" or "persistent".'
        )
    thread_id = thread.get("id")
    if mode == "persistent" and (not isinstance(thread_id, str) or not thread_id.strip()):
        raise ValueError("define_schedule(...) requires `thread['id']` for persistent schedules.")


_DELIVER_TO_DEPRECATION = (
    "define_schedule(deliver_to=...) is deprecated; use auto_post with a channel target, "
    'such as auto_post={"provider": "slack", "conversation_id": "C123"}.'
)
_BOTH_DELIVERIES = "define_schedule(...) takes `auto_post` or `deliver_to`, not both."
_SCHEDULE_TARGETS = (
    "define_schedule(...) requires `auto_post` to be False or a Slack target for now."
)


def _slack_target_of(fields: Mapping[str, object]) -> SlackChannelTarget | None:
    """A ``{"provider": "slack", "conversation_id", "thread_ts"?, "channel"?}`` literal."""
    known = {"provider", "conversation_id", "thread_ts", "channel", "code_channel"}
    if fields.get("provider") != "slack" or not set(fields) <= known:
        return None
    try:
        return SlackChannelTarget(
            conversation_id=cast("str", fields.get("conversation_id")),
            thread_ts=cast("str | None", fields.get("thread_ts")),
            channel=cast("str | None", fields.get("channel")),
        )
    except (TypeError, ValueError):
        return None


def _deliver_to_of(auto_post: object) -> ScheduleDeliveryTarget | None:
    """``auto_post`` as the ``deliver_to`` MDA stores, which deploy reads; ``False`` is none.

    Only Slack schedules post their answer, for now.
    """
    if auto_post is False:
        return None
    if isinstance(auto_post, Mapping):
        # The literal a schedule file writes, since deploy reads schedules statically.
        auto_post = _slack_target_of(auto_post)
    if not isinstance(auto_post, SlackChannelTarget):
        raise ValueError(_SCHEDULE_TARGETS)
    channel = auto_post.channel or auto_post.provider
    thread_ts = auto_post.thread_ts
    ids = (channel, auto_post.conversation_id, "x" if thread_ts is None else thread_ts)
    if not all(isinstance(value, str) and value.strip() for value in ids):
        raise ValueError(_SCHEDULE_TARGETS)
    to = cast(
        "ScheduleChannelDestination",
        {"type": "provider_conversation", "conversation_id": auto_post.conversation_id}
        if auto_post.thread_ts is None
        else {
            "type": "provider_thread",
            "conversation_id": auto_post.conversation_id,
            "thread_id": auto_post.thread_ts,
        },
    )
    deliver_to: ScheduleDeliveryTarget = {"channel": channel.strip(), "to": to}
    try:
        _validate_deliver_to(deliver_to)
    except ValueError:
        raise ValueError(_SCHEDULE_TARGETS) from None
    return deliver_to


def _validate_deliver_to(deliver_to: ScheduleDeliveryTarget | object) -> None:
    if not isinstance(deliver_to, dict):
        raise ValueError("define_schedule(...) requires `deliver_to` to be a mapping.")
    channel = deliver_to.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError(
            "define_schedule(...) requires `deliver_to['channel']` to be a non-empty string."
        )
    destination = deliver_to.get("to")
    if not isinstance(destination, Mapping):
        raise ValueError("define_schedule(...) requires `deliver_to['to']`.")
    if destination.get("type") == "current_thread":
        raise ValueError(
            'define_schedule(...) rejects `deliver_to["to"]["type"]: "current_thread"`.'
        )
    # Validate the destination shape early rather than at delivery time.
    normalize_channel_destination(cast("Mapping[str, object]", destination))
