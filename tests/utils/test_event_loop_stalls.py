"""A request that holds the event loop is logged, naming what held it."""

import asyncio
import logging
import time

import pytest
from langgraph_api.logging import set_logging_context

from openswe.utils.event_loop import watch_for_stalls


def _hog_the_loop() -> None:
    time.sleep(1.2)


async def _serve_request() -> None:
    set_logging_context({"thread_id": "thread-1", "endpoint": "walkthrough"})
    _hog_the_loop()


async def test_a_long_stall_logs_before_and_after_it_ends(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("EVENT_LOOP_STALL_MS", "100")
    caplog.set_level(logging.WARNING, logger="openswe.utils.event_loop")

    with watch_for_stalls():
        await asyncio.sleep(0.2)
        await asyncio.create_task(_serve_request())
        await asyncio.sleep(0.3)

    ongoing, ended = [r for r in caplog.records if r.message == "Event loop stalled"]
    assert (ongoing.stall_recovered, ended.stall_recovered) == (False, True)
    assert 1000 <= ongoing.stall_ms < ended.stall_ms
    for stall in (ongoing, ended):
        assert "_hog_the_loop" in stall.loop_stack
        assert stall.thread_id == "thread-1"
        assert stall.endpoint == "walkthrough"
