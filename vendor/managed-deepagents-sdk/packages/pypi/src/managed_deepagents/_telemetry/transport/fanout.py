"""Hands each event to every configured sink."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from managed_deepagents._telemetry.event import TelemetryEvent


class QueuedTransport(Protocol):
    def send(self, event: TelemetryEvent) -> None: ...

    def idle(self, timeout: float = ...) -> bool: ...


class FanoutTransport:
    """Each sink keeps its own queue and retries, so one failing or slow
    backend never holds up another.
    """

    def __init__(self, sinks: Sequence[QueuedTransport]) -> None:
        self._sinks = tuple(sinks)

    def send(self, event: TelemetryEvent) -> None:
        for sink in self._sinks:
            try:
                sink.send(event)
            except Exception:  # noqa: BLE001, S112 - one sink must not starve the others
                continue

    def idle(self, timeout: float = 1.0) -> bool:
        # A list, not a generator: every sink waits, even after one times out.
        return all([sink.idle(timeout) for sink in self._sinks])  # noqa: C419


def fanout_transport(sinks: Sequence[QueuedTransport | None]) -> FanoutTransport | None:
    """Absent when no sink is configured, so telemetry stays off."""
    present = [sink for sink in sinks if sink is not None]
    return FanoutTransport(present) if present else None
