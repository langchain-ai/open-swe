import logging
import uuid
from unittest.mock import AsyncMock

import pytest

from openswe.slack import failures


@pytest.fixture(autouse=True)
def clear_status(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    status = AsyncMock(return_value=True)
    monkeypatch.setattr(failures, "set_slack_thread_status", status)
    return status


def _target() -> failures.SlackRequestTarget:
    return failures.SlackRequestTarget(channel_id="C1", thread_ts="1.0", event_id="Ev1")


async def test_unexpected_failure_replies_with_a_uuid7_error_id_that_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, clear_status: AsyncMock
) -> None:
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(failures, "post_slack_thread_reply", post_reply)

    with caplog.at_level(logging.ERROR, logger=failures.logger.name):
        error_id = await failures.report_slack_failure(_target(), RuntimeError("boom"))

    assert uuid.UUID(error_id).version == 7
    post_reply.assert_awaited_once()
    clear_status.assert_awaited_once_with("C1", "1.0", "")
    await_args = post_reply.await_args
    assert await_args is not None
    assert await_args.args[:2] == ("C1", "1.0")
    assert "unexpected error" in await_args.args[2]
    assert f"Error ID: `{error_id}`" in await_args.args[2]

    [record] = [r for r in caplog.records if r.getMessage() == "Slack request failed"]
    assert record.error_id == error_id  # type: ignore[attr-defined]
    assert record.slack_channel_id == "C1"  # type: ignore[attr-defined]
    assert record.slack_thread_ts == "1.0"  # type: ignore[attr-defined]
    assert record.slack_event_id == "Ev1"  # type: ignore[attr-defined]
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)


async def test_status_failure_still_delivers_error_reply(
    monkeypatch: pytest.MonkeyPatch, clear_status: AsyncMock
) -> None:
    clear_status.side_effect = RuntimeError("status unavailable")
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(failures, "post_slack_thread_reply", post_reply)

    error_id = await failures.report_slack_failure(_target(), RuntimeError("boom"))

    assert uuid.UUID(error_id).version == 7
    post_reply.assert_awaited_once()


async def test_run_slack_task_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(failures, "post_slack_thread_reply", post_reply)

    async def task() -> None:
        raise RuntimeError("boom")

    await failures.run_slack_task(_target(), task())

    post_reply.assert_awaited_once()
