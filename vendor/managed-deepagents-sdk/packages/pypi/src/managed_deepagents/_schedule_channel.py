"""The thread a schedule is created from: its origin is where the schedule posts."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace

from langgraph.runtime import get_runtime

from managed_deepagents._channels.binding import resolve_channel_binding
from managed_deepagents._identity_runtime import _field
from managed_deepagents._managed_thread import RunThread, current_run_thread


def ambient_context() -> object:
    try:
        return get_runtime().context
    except RuntimeError:  # Outside a LangGraph run there is no ambient runtime.
        return None


def schedule_thread(configurable: Mapping[str, object] | None) -> RunThread:
    """``runtime.thread`` of the run creating a schedule, with its origin's route."""
    context = ambient_context()
    if (
        _field(context, "mda_channel") is not None
        and resolve_channel_binding(context, configurable) is None
    ):
        raise ValueError("Cannot schedule from an invalid channel context")
    return current_run_thread(SimpleNamespace(context=context), configurable)
