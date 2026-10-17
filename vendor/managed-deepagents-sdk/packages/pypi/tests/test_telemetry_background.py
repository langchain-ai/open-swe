"""Delivery queue and process shutdown behavior."""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from managed_deepagents._telemetry.background import MAX_PENDING_EVENTS, BackgroundTelemetryClient
from managed_deepagents._telemetry.client import TelemetryClient

if TYPE_CHECKING:
    from managed_deepagents._telemetry.event import TelemetryEvent

EVENT: TelemetryEvent = {
    "event_id": "event",
    "event_type": "test",
    "schema_version": "test",
    "created_at": "2026-09-21T00:00:00.000Z",
    "distinct_id": "test",
}


class SlowTransport:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.sent: list[TelemetryEvent] = []

    def send(self, event: TelemetryEvent) -> None:
        self.started.set()
        self.release.wait()
        self.sent.append(event)


def test_queue_is_bounded_and_drops_excess_events() -> None:
    transport = SlowTransport()
    client = BackgroundTelemetryClient(TelemetryClient(transport))
    try:
        client.record(EVENT)
        assert transport.started.wait(timeout=2)
        for _ in range(MAX_PENDING_EVENTS + 1):
            client.record(EVENT)
        assert not client.flush(timeout=0)
        transport.release.set()
        assert client.flush(timeout=5)
        assert len(transport.sent) == MAX_PENDING_EVENTS + 1
    finally:
        transport.release.set()
        client.close()


def test_close_has_a_deadline_and_rejects_new_events() -> None:
    transport = SlowTransport()
    client = BackgroundTelemetryClient(TelemetryClient(transport))
    try:
        client.record(EVENT)
        assert transport.started.wait(timeout=2)
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(client.close, 0.01).result(timeout=1)
        assert transport.sent == []
        client.record(EVENT)
        transport.release.set()
        assert client.flush(timeout=2)
        assert transport.sent == [EVENT]
    finally:
        transport.release.set()
        client.close()


def test_failed_delivery_does_not_stop_later_events() -> None:
    sent: list[str] = []

    class Transport:
        def send(self, event: TelemetryEvent) -> None:
            if event["event_id"] == "failed":
                raise RuntimeError("offline")
            sent.append(event["event_id"])

    client = BackgroundTelemetryClient(TelemetryClient(Transport()))
    try:
        client.record({**EVENT, "event_id": "failed"})
        client.record(EVENT)
        assert client.flush(timeout=2)
        assert sent == ["event"]
    finally:
        client.close()


@pytest.mark.parametrize("stalled", [False, True])
def test_process_exit_flushes_with_a_deadline(tmp_path: Path, stalled: bool) -> None:
    output = tmp_path / "delivered"
    script = """
import socket
import sys
import time
from threading import Event
from pathlib import Path
from managed_deepagents._telemetry.client import TelemetryClient
from managed_deepagents._telemetry.transport import PosthogTransport
from managed_deepagents._telemetry.run_wrapper import (
    observe_run, set_run_telemetry_client_for_tests,
)
from tests.test_telemetry_run_wrapper import _counter, _pending

class Transport:
    def send(self, event):
        time.sleep(0.05)
        Path(sys.argv[1]).write_text(event["outcome"])

started = Event()
def stalled_dns(*args, **kwargs):
    started.set()
    Event().wait()

transport = Transport()
if sys.argv[2] == "stalled":
    socket.getaddrinfo = stalled_dns
    transport = PosthogTransport(api_key="test", host="https://telemetry.invalid")
set_run_telemetry_client_for_tests(TelemetryClient(transport))
assert observe_run(_counter(), _pending()).invoke({"n": 0}) == {"n": 1}
if sys.argv[2] == "stalled":
    assert started.wait(2)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(output), "stalled" if stalled else "ready"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if stalled:
        assert not output.exists()
    else:
        assert output.read_text() == "success"
