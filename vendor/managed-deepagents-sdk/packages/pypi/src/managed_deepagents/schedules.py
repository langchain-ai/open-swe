"""Managed ``schedules/`` authoring interface for the Python ecosystem.

A schedule is a proactive, time-triggered agent run. Files under
``schedules/*.py`` export a named ``schedule`` created with
:func:`define_schedule`; the compiler materializes those declarations into
LangGraph cron entries.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, cast

from pydantic import ConfigDict, StringConstraints, with_config
from typing_extensions import TypedDict

from managed_deepagents._channels.types import normalize_channel_destination

JsonValue = object
_NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@with_config(ConfigDict(extra="forbid"))
class ScheduleThreadEphemeral(TypedDict, total=False):
    mode: Literal["ephemeral"]


@with_config(ConfigDict(extra="forbid"))
class ScheduleThreadPersistent(TypedDict):
    mode: Literal["persistent"]
    id: str


ScheduleThreadConfig = ScheduleThreadEphemeral | ScheduleThreadPersistent


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
    auto_post: bool


class ScheduleConfigRequired(TypedDict):
    cron: str


class ScheduleConfig(ScheduleConfigRequired, total=False):
    timezone: str
    prompt: str
    input: JsonValue
    thread: ScheduleThreadConfig
    deliver_to: ScheduleDeliveryTarget
    context: dict[str, JsonValue]


__all__ = [
    "ScheduleConfig",
    "ScheduleDefinition",
    "ScheduleDeliveryTarget",
    "ScheduleThreadConfig",
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
    thread: ScheduleThreadConfig | None = None,
    deliver_to: ScheduleDeliveryTarget | None = None,
    context: Mapping[str, JsonValue] | None = None,
) -> ScheduleDefinition:
    """Declare a managed schedule that MDA provisions as a LangGraph cron."""

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
        _validate_deliver_to(deliver_to)
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


def _validate_thread(thread: ScheduleThreadConfig | object | None) -> None:
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
