"""Resolving a thread's conversation surface."""

from typing import Any

from agent.source_context import SlackThreadRef, SourceContext
from agent.surfaces.base import WEB_SURFACE, Surface, SurfaceKind
from agent.surfaces.slack import (
    CODE_CHANNEL_PROMPT_SECTION,
    SlackChannelSurface,
    SlackThreadSurface,
)
from agent.utils.slack_code_channels import is_code_channel_session

__all__ = [
    "CODE_CHANNEL_PROMPT_SECTION",
    "WEB_SURFACE",
    "SlackChannelSurface",
    "SlackThreadSurface",
    "Surface",
    "SurfaceKind",
    "resolve_surface",
    "slack_surface",
    "surface_from_metadata",
]


def slack_surface(ref: SlackThreadRef | None) -> Surface:
    """The surface for a Slack location.

    ``surface`` is absent on every location written before it existed, so the
    channel-session timestamp remains the fallback signal.
    """
    if ref is None or not ref.channel_id:
        return WEB_SURFACE
    kind = ref.surface or (
        "slack_channel" if is_code_channel_session(ref.thread_ts) else "slack_thread"
    )
    if kind == "slack_channel":
        return SlackChannelSurface(ref.channel_id, ref.reply_thread_ts)
    return SlackThreadSurface(ref.channel_id, ref.thread_ts)


def resolve_surface(context: SourceContext | None) -> Surface:
    """The surface a thread's conversation lives on."""
    if context is None:
        return WEB_SURFACE
    return slack_surface(context.slack_thread)


def surface_from_metadata(metadata: Any) -> Surface:
    """The surface for a thread, from its LangGraph metadata."""
    return resolve_surface(SourceContext.from_metadata(metadata))
