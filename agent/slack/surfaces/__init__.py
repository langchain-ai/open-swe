"""Resolving which Slack session a thread's conversation lives in."""

from typing import Any

from agent.slack.code_channels import is_code_channel_session
from agent.slack.surfaces.base import SlackSurface
from agent.slack.surfaces.channel import CODE_CHANNEL_PROMPT_SECTION, SlackChannelSurface
from agent.slack.surfaces.thread import SlackThreadSurface
from agent.source_context import SlackThreadRef, SourceContext

__all__ = [
    "CODE_CHANNEL_PROMPT_SECTION",
    "SlackChannelSurface",
    "SlackSurface",
    "SlackThreadSurface",
    "resolve_surface",
    "slack_surface",
    "surface_from_metadata",
]


def slack_surface(ref: SlackThreadRef | None) -> SlackSurface | None:
    """The Slack session a location belongs to, or nothing if it is not in Slack.

    ``surface`` is absent on every location written before it existed, so the
    channel-session timestamp remains the fallback signal.
    """
    if ref is None or not ref.channel_id:
        return None
    kind = ref.surface or (
        "slack_channel" if is_code_channel_session(ref.thread_ts) else "slack_thread"
    )
    if kind == "slack_channel":
        return SlackChannelSurface(ref.channel_id, ref.reply_thread_ts)
    return SlackThreadSurface(ref.channel_id, ref.thread_ts)


def resolve_surface(context: SourceContext | None) -> SlackSurface | None:
    """The Slack session a thread's conversation lives in, if it lives in Slack."""
    if context is None:
        return None
    return slack_surface(context.slack_thread)


def surface_from_metadata(metadata: Any) -> SlackSurface | None:
    """The Slack session for a thread, from its LangGraph metadata."""
    return resolve_surface(SourceContext.from_metadata(metadata))
