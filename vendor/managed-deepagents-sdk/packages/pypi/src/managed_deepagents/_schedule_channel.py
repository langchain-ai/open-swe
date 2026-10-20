"""Capture the authenticated channel for a later schedule run."""

from __future__ import annotations

from collections.abc import Mapping

from langgraph.runtime import get_runtime

from managed_deepagents._channels.binding import ChannelBinding, resolve_channel_binding
from managed_deepagents._identity_runtime import _field


def ambient_context() -> object:
    try:
        return get_runtime().context
    except RuntimeError:  # Outside a LangGraph run there is no ambient runtime.
        return None


def capture_schedule_channel(
    configurable: Mapping[str, object] | None, *, new_thread: bool
) -> ChannelBinding | None:
    context = ambient_context()
    binding = resolve_channel_binding(context, configurable)
    if binding is None:
        if _field(context, "mda_channel") is not None:
            raise ValueError("Cannot schedule from an invalid channel context")
        return None
    binding["event"] = {"type": "schedule", "new_thread": new_thread}
    return binding
