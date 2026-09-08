"""LangGraph v3 streaming helpers."""

from collections.abc import Mapping
from typing import Any

TERMINAL_LIFECYCLE_EVENTS = frozenset({"completed", "failed", "interrupted"})


def root_lifecycle(event: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return the run ID and phase for a root lifecycle event."""
    if event.get("method") != "lifecycle":
        return None
    params = event.get("params")
    if not isinstance(params, Mapping) or params.get("namespace") != []:
        return None
    data = params.get("data")
    event_id = event.get("event_id")
    if not isinstance(data, Mapping) or not isinstance(event_id, str):
        return None
    phase = data.get("event")
    parts = event_id.split(":", 2)
    if (
        not isinstance(phase, str)
        or len(parts) != 3
        or parts[0] != "synth"
        or not parts[1]
        or not parts[2].startswith("lc|")
    ):
        return None
    return parts[1], phase
