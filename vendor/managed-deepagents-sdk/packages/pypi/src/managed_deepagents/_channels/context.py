"""Pass the original provider event into run context."""

from typing import cast

from managed_deepagents._channels.binding import ChannelBinding
from managed_deepagents._channels.trigger.types import (
    TriggerChannelRunEvent,
    TriggerChannelSlackUserPromptResponseEvent,
)
from managed_deepagents.run_context import NormalizedChannelEvent


def normalized_user_prompt_response(
    event: TriggerChannelSlackUserPromptResponseEvent,
) -> NormalizedChannelEvent:
    """The provider-neutral view of a prompt response, also used as its resume value."""
    interaction = event["interaction"]
    normalized: NormalizedChannelEvent = {
        "provider": event["provider"],
        "action": interaction["action"],
    }
    if "value" in interaction:
        normalized["value"] = interaction["value"]
    correlation_id = interaction.get("correlation_id")
    if correlation_id is not None:
        normalized["correlation_id"] = correlation_id
    return normalized


def trigger_channel_binding(
    *, provider: str, name: str, event: TriggerChannelRunEvent
) -> ChannelBinding:
    """Build current typed facts and a private route without identity tokens."""
    if event["type"] == "message":
        typed = {"type": "message", "messages": event["input"]["messages"]}
    elif event["type"] == "interrupt_resume":
        typed = {"type": "interrupt_resume", "resume": event["interaction"]["resume"]}
    else:
        interaction = event["interaction"]
        typed = {
            "type": "user_prompt_response",
            "action": interaction["action"],
            **{key: interaction[key] for key in ("value", "correlation_id") if key in interaction},
        }
    return cast(
        "ChannelBinding",
        {
            "name": name,
            "provider": provider,
            "source_thread_key": event["source_thread_key"],
            "delivery_id": event["delivery_id"],
            "transport": "trigger_server",
            "event": typed,
            **({"raw_event": event["raw_event"]} if event.get("raw_event") is not None else {}),
            "target": {
                "channel_id": event["channel_id"],
                "address": event["address"],
                "source_thread_key": event["source_thread_key"],
            },
        },
    )
