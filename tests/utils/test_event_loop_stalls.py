"""A request that holds the event loop is logged once, naming what held it."""

import asyncio
import logging
import time

import pytest
from langgraph_api.logging import set_logging_context

from openswe.utils.event_loop import watch_for_stalls


def _hog_the_loop() -> None:
    time.sleep(0.6)


async def _serve_request() -> None:
    set_logging_context({"thread_id": "thread-1", "endpoint": "walkthrough"})
    _hog_the_loop()


async def test_a_stall_logs_once_with_the_blocking_stack(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("EVENT_LOOP_STALL_MS", "200")
    caplog.set_level(logging.WARNING, logger="openswe.utils.event_loop")

    with watch_for_stalls():
        await asyncio.sleep(0.2)
        await asyncio.create_task(_serve_request())
        await asyncio.sleep(0.3)

    [stall] = [record for record in caplog.records if record.message == "Event loop stalled"]
    assert stall.stall_ms >= 500
    assert "_hog_the_loop" in stall.loop_stack
    assert stall.thread_id == "thread-1"
    assert stall.endpoint == "walkthrough"
