import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from openswe import event_claims
from openswe.middleware.stale_workspace import StaleWorkspaceReporter
from openswe.slack import client as slack_utils
from openswe.slack.alerts import SlackAlertLogger
from openswe.utils.user_messages import warning
from openswe.workspaces.store import Workspace


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
async def test_operational_alerts_share_a_channel_cooldown(slack_api, memory_event_claims) -> None:
    slack_api.respond({"ok": True, "ts": "2.0"})
    results = await asyncio.gather(
        *(
            SlackAlertLogger("C1").warning("Image stale", details={"workspace": str(i)})
            for i in range(5)
        )
    )
    assert results.count(True) == 1
    assert len(slack_api.calls) == 1
    memory_event_claims.held.clear()
    assert await SlackAlertLogger("C1").warning("Another issue", details={})
    assert len(slack_api.calls) == 2


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
        (timedelta(hours=6, minutes=17), "6h 17m"),
        (timedelta(days=2, hours=3, minutes=4), "2d 3h 4m"),
        (None, "unknown (capture timestamp missing or invalid)"),
    ],
)
async def test_stale_workspace_alerts_skip_fresh_images(
    slack_api, memory_event_claims, monkeypatch, caplog, image_age, expected_age
) -> None:
    monkeypatch.delenv("SLACK_ALERTS_CHANNEL_ID", raising=False)
    slack_api.respond({"ok": True, "ts": "2.0"})
    workspace = Workspace(
        slug="default",
        last_captured_at=datetime.now(UTC).isoformat(),
        update_script="git pull",
        snapshot_status="ready",
        snapshot_id="snapshot-1",
    )
    await StaleWorkspaceReporter.report("thread-1", workspace)
    assert not slack_api.calls
    workspace.last_captured_at = (
        (datetime.now(UTC) - image_age).isoformat() if image_age is not None else None
    )
    await StaleWorkspaceReporter.report("thread-1", workspace)
    assert not slack_api.calls
    [record] = [r for r in caplog.records if r.name == "openswe.slack.alerts"]
    assert record.getMessage() == "Run booted a stale workspace image"
    assert record.__dict__["Image age"] == expected_age
    monkeypatch.setenv("SLACK_ALERTS_CHANNEL_ID", "C_DEPLOYMENT_ALERTS")
    claim = AsyncMock(wraps=event_claims.claim)
    monkeypatch.setattr(event_claims, "claim", claim)
    await asyncio.gather(
        *(StaleWorkspaceReporter.report(f"thread-{i}", workspace) for i in range(5))
    )
    [(_, params)] = slack_api.calls
    assert params["channel"] == "C_DEPLOYMENT_ALERTS"
    assert params["text"] == f"⚠️ *Run booted a stale workspace image (age: {expected_age})*"
    assert params["blocks"][0]["text"]["text"] == params["text"]
    assert "Workspace" not in params["text"]
    details = params["blocks"][1]
    assert details["type"] == "context"
    detail_text = details["elements"][0]["text"]
    assert "*Workspace:* default" in detail_text
    assert "*Run:*" in detail_text
    assert "thread-0" in detail_text
    assert "None" not in detail_text
    claim.assert_awaited_with("slack-alert", "C_DEPLOYMENT_ALERTS", ttl=timedelta(hours=8))
    workspace.slug = "another-workspace"
    await StaleWorkspaceReporter.report("thread-other", workspace)
    assert len(slack_api.calls) == 1
    memory_event_claims.held.clear()
    await StaleWorkspaceReporter.report("thread-later", workspace)
    assert len(slack_api.calls) == 2
