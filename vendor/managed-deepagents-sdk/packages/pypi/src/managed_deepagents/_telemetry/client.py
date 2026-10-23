from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

from managed_deepagents._telemetry.event import TelemetryEvent

if TYPE_CHECKING:
    from managed_deepagents._telemetry.transport.types import TelemetryTransport

E = TypeVar("E", bound=TelemetryEvent)


class TelemetryClient(Generic[E]):
    """An absent transport means telemetry is off, so no call site has to check."""

    def __init__(self, transport: TelemetryTransport | None) -> None:
        self._transport = transport

    def record(self, event: E) -> None:
        if self._transport is None:
            return
        try:
            self._transport.send(event)
        except Exception:  # noqa: BLE001 - a failed send must not affect the run
            return
