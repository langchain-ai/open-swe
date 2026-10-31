"""Best-effort recording contract shared with the Node and Rust runtimes."""

from __future__ import annotations

from managed_deepagents._telemetry import TelemetryClient, TelemetryEvent

EVENT: TelemetryEvent = {
    "event_id": "22222222-2222-4222-8222-222222222222",
    "event_type": "fixture",
    "schema_version": "2026-09-15",
    "created_at": "1970-01-01T00:00:01.234Z",
    "distinct_id": "33333333-3333-4333-8333-333333333333",
}


class _RecordingTransport:
    def __init__(self, *, fails: bool = False) -> None:
        self.sent: list[TelemetryEvent] = []
        self._fails = fails

    def send(self, event: TelemetryEvent) -> None:
        self.sent.append(event)
        if self._fails:
            raise RuntimeError("transport failure")


def test_sends_the_event_through_the_transport_once() -> None:
    transport = _RecordingTransport()

    TelemetryClient(transport).record(EVENT)

    assert transport.sent == [EVENT]


def test_swallows_a_transport_failure() -> None:
    transport = _RecordingTransport(fails=True)

    TelemetryClient(transport).record(EVENT)

    assert len(transport.sent) == 1


def test_does_nothing_without_a_transport() -> None:
    TelemetryClient(None).record(EVENT)
