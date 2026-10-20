"""Backend for analytics egress."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from managed_deepagents._telemetry.event import TelemetryEvent


class TelemetryTransport(Protocol):
    def send(self, event: TelemetryEvent) -> None: ...
