"""One log context per inbound event, completed after scheduled work settles."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from managed_deepagents import _ingress
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from tests.conftest import CapturedLogs

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


def test_completes_once_after_scheduled_work_under_the_requests_trace_id(
    captured_logs: CapturedLogs,
) -> None:
    async def main() -> None:
        finish = asyncio.Event()

        async def work() -> None:
            await finish.wait()
            logger.info("reply posted")

        async def handle() -> object:
            _ingress.schedule_ingress_work(work, label="observe")
            return SimpleNamespace(status_code=202)

        response = await _ingress.with_ingress_log_context(
            f"00-{TRACE_ID}-00f067aa0ba902b7-01", {"channel": "slack"}, handle
        )
        await asyncio.sleep(0)
        assert response.status_code == 202
        assert captured_logs.records == []

        finish.set()
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(main())

    records = captured_logs.records
    assert [(record.body, record.trace_id) for record in records] == [
        ("reply posted", TRACE_ID),
        ("ingress completed", TRACE_ID),
    ]
    assert records[-1].attributes["http.status_code"] == 202
    assert records[-1].attributes["channel"] == "slack"


def test_records_a_failed_request_as_a_500(
    captured_logs: CapturedLogs,
) -> None:
    async def handle() -> object:
        raise RuntimeError("boom")

    async def main() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            await _ingress.with_ingress_log_context(None, {}, handle)
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(main())

    assert len(captured_logs.records) == 1
    assert captured_logs.records[0].attributes["http.status_code"] == 500


def test_a_background_failure_names_the_error_type_on_stdout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def work() -> None:
        raise TypeError("secret payload")

    async def main() -> None:
        _ingress.schedule_ingress_work(work, label="observe")
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(main())

    assert [record.getMessage() for record in caplog.records] == [
        "[mda] observe failed (TypeError)"
    ]
