import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from openswe import event_claims
from openswe.execution import ExecutionProfile
from openswe.middleware.stale_execution import StaleExecutionReporter
from openswe.sandboxes.providers.langsmith import LangSmithCreateOptions
from openswe.slack import client as slack_utils
from openswe.slack.alerts import SlackAlertLogger
from openswe.utils.user_messages import warning


@pytest.mark.asyncio
async def test_slack_warning_post_logs_error(
    slack_api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slack_api.respond({"ok": True, "ts": "2.0"})

    with (
        caplog.at_level(logging.ERROR, logger=slack_utils.logger.name),
    ):
        result = await slack_utils._post_slack_message_with_ts(
            "C1",
            warning("Open SWE reached its maximum step limit."),
            thread_ts="1.0",
        )

    assert result == "2.0"
    assert "Sent automated warning message to Slack thread C1/1.0" in caplog.text
    assert "⚠️ Open SWE reached its maximum step limit." in caplog.text


@pytest.mark.asyncio
async def test_plain_slack_post_does_not_log_warning_error(
    slack_api,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slack_api.respond({"ok": True, "ts": "2.0"})

    with (
        caplog.at_level(logging.ERROR, logger=slack_utils.logger.name),
    ):
        result = await slack_utils._post_slack_message_with_ts(
            "C1",
            "Normal Slack reply",
            thread_ts="1.0",
        )

    assert result == "2.0"
    assert "Sent automated warning message to Slack" not in caplog.text


@pytest.mark.asyncio
async def test_operational_alerts_share_a_channel_cooldown(
    slack_api, memory_event_claims, monkeypatch
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example")
    slack_api.respond({"ok": True, "ts": "2.0"})
    results = await asyncio.gather(
        *(
            SlackAlertLogger("C1").warning("Image stale", details={"snapshot": str(i)})
            for i in range(5)
        )
    )
    assert results.count(True) == 1
    assert len(slack_api.calls) == 1
    memory_event_claims.held.clear()
    url = "https://github.com/acme/app/pull/7"
    assert await SlackAlertLogger("C1").warning("Another issue", details={"pull_request": url})
    assert len(slack_api.calls) == 2
    details = slack_api.calls[-1][1]["blocks"][1]["elements"][0]["text"]
    assert "https://openswe.example/agents/reviews/acme/app/7" in details
    assert f"<{url}|(GitHub)>" in details


@pytest.mark.asyncio
async def test_operational_alert_failure_does_not_break_the_run(
    slack_api, memory_event_claims, monkeypatch, caplog
) -> None:
    slack_api.respond({"ok": False, "error": "channel_not_found"})
    assert not await SlackAlertLogger("C1").warning("Image stale", details={})
    assert "Operational Slack alert failed" in caplog.text
    assert not await SlackAlertLogger("C1").warning("Image stale", details={})
    assert len(slack_api.calls) == 1
    monkeypatch.setattr(event_claims, "claim", AsyncMock(side_effect=RuntimeError("database down")))
    assert not await SlackAlertLogger("C1").warning("Image stale", details={})
    assert "database down" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image_age", "expected_age"),
    [
        (timedelta(hours=9, minutes=9), "9h 9m"),
        (timedelta(days=2, hours=3, minutes=4), "2d 3h 4m"),
        (None, "unknown (capture timestamp missing or invalid)"),
    ],
)
async def test_stale_execution_alerts_skip_fresh_images(
    slack_api, memory_event_claims, monkeypatch, caplog, image_age, expected_age
) -> None:
    monkeypatch.delenv("SLACK_ALERTS_CHANNEL_ID", raising=False)
    slack_api.respond({"ok": True, "ts": "2.0"})
    profile = ExecutionProfile(
        uses_defaults=True,
        create=LangSmithCreateOptions.resolve(snapshot_id="snapshot-1", create_params={}),
        captured_at=datetime.now(UTC).isoformat(),
        refresh_enabled=True,
    )
    await StaleExecutionReporter.report("thread-1", profile)
    profile = profile.model_copy(
        update={"captured_at": (datetime.now(UTC) - timedelta(hours=7, minutes=59)).isoformat()}
    )
    await StaleExecutionReporter.report("thread-1", profile)
    assert not slack_api.calls
    assert not [r for r in caplog.records if r.name == "openswe.slack.alerts"]
    profile = profile.model_copy(
        update={"captured_at": (datetime.now(UTC) - image_age).isoformat() if image_age else None}
    )
    await StaleExecutionReporter.report("thread-1", profile)
    assert not slack_api.calls
    [record] = [r for r in caplog.records if r.name == "openswe.slack.alerts"]
    assert record.getMessage() == "Run booted a stale Robin-wide execution image"
    assert record.__dict__["Image age"] == expected_age
    monkeypatch.setenv("SLACK_ALERTS_CHANNEL_ID", "C_DEPLOYMENT_ALERTS")
    claim = AsyncMock(wraps=event_claims.claim)
    monkeypatch.setattr(event_claims, "claim", claim)
    await asyncio.gather(*(StaleExecutionReporter.report(f"thread-{i}", profile) for i in range(5)))
    [(_, params)] = slack_api.calls
    assert params["channel"] == "C_DEPLOYMENT_ALERTS"
    assert (
        params["text"] == f"⚠️ *Run booted a stale Robin-wide execution image (age: {expected_age})*"
    )
    assert params["blocks"][0]["text"]["text"] == params["text"]
    assert "Workspace" not in params["text"]
    details = params["blocks"][1]
    assert details["type"] == "context"
    detail_text = details["elements"][0]["text"]
    assert "*Snapshot:* snapshot-1" in detail_text
    assert "*Run:*" in detail_text
    assert "thread-0" in detail_text
    assert "None" not in detail_text
    claim.assert_awaited_with("slack-alert", "C_DEPLOYMENT_ALERTS", ttl=timedelta(hours=8))
    another = profile.model_copy(
        update={"create": profile.create.model_copy(update={"snapshot_id": "snapshot-2"})}
    )
    await StaleExecutionReporter.report("thread-other", another)
    assert len(slack_api.calls) == 1
    memory_event_claims.held.clear()
    await StaleExecutionReporter.report("thread-later", profile)
    assert len(slack_api.calls) == 2
