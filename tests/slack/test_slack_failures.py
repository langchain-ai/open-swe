import logging
import uuid
from unittest.mock import AsyncMock

import pytest

from agent.slack import failures


def _target() -> failures.SlackRequestTarget:
    return failures.SlackRequestTarget(channel_id="C1", thread_ts="1.0", event_id="Ev1")


async def test_unexpected_failure_replies_with_a_uuid7_error_id_that_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(failures, "post_slack_thread_reply", post_reply)

    with caplog.at_level(logging.ERROR, logger=failures.logger.name):
        error_id = await failures.report_slack_failure(_target(), RuntimeError("boom"))

    assert uuid.UUID(error_id).version == 7
    post_reply.assert_awaited_once()
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


async def test_request_error_replies_with_its_own_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(failures, "post_slack_thread_reply", post_reply)

    with caplog.at_level(logging.WARNING, logger=failures.logger.name):
        error_id = await failures.report_slack_failure(
            _target(), failures.SlackRequestError("Pick a repository first.")
        )

    await_args = post_reply.await_args
    assert await_args is not None
    assert await_args.args[2] == f"⚠️ Pick a repository first.\nError ID: `{error_id}`"
    [record] = [r for r in caplog.records if r.getMessage() == "Slack request rejected"]
    assert record.levelno == logging.WARNING
    assert record.error_id == error_id  # type: ignore[attr-defined]


async def test_failed_reply_still_returns_error_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        failures, "post_slack_thread_reply", AsyncMock(side_effect=RuntimeError("slack down"))
    )

    error_id = await failures.report_slack_failure(_target(), RuntimeError("boom"))

    assert uuid.UUID(error_id).version == 7


async def test_answer_slack_request_turns_failure_into_error_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(failures, "post_slack_thread_reply", post_reply)

    async def handle() -> dict[str, str]:
        raise RuntimeError("boom")

    response = await failures.answer_slack_request(_target(), handle)

    assert response["status"] == "error"
    assert uuid.UUID(response["error_id"]).version == 7
    post_reply.assert_awaited_once()


async def test_answer_slack_request_passes_through_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(failures, "post_slack_thread_reply", post_reply)

    async def handle() -> dict[str, str]:
        return {"status": "accepted"}

    assert await failures.answer_slack_request(_target(), handle) == {"status": "accepted"}
    post_reply.assert_not_awaited()


async def test_run_slack_task_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    post_reply = AsyncMock(return_value=True)
    monkeypatch.setattr(failures, "post_slack_thread_reply", post_reply)

    async def task() -> None:
        raise RuntimeError("boom")

    await failures.run_slack_task(_target(), task())

    post_reply.assert_awaited_once()
