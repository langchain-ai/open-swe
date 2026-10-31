"""Shared test setup."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from managed_deepagents._telemetry.otel.exporter import set_log_sink_for_tests

if TYPE_CHECKING:
    from collections.abc import Iterator

    from managed_deepagents._telemetry.otel import LogRecord


@pytest.fixture(autouse=True)
def _no_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt the suite out of analytics.

    Compiling an agent builds a transport and the suite compiles many. Tests
    that assert on emission inject a client instead.
    """
    monkeypatch.setenv("MDA_TELEMETRY_DISABLED", "1")


class CapturedLogs:
    """A log sink that keeps what is pushed."""

    enabled = True

    def __init__(self) -> None:
        self.records: list[LogRecord] = []

    def push(self, record: LogRecord) -> None:
        self.records.append(record)

    def idle(self, timeout: float) -> bool:
        return True

    def shutdown(self, timeout: float) -> None:
        pass


@pytest.fixture
def captured_logs() -> Iterator[CapturedLogs]:
    """Route log records to a capturing sink for the test."""
    sink = CapturedLogs()
    set_log_sink_for_tests(sink)
    yield sink
    set_log_sink_for_tests(None)
