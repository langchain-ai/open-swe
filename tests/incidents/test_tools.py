"""The manage_incident tool on Slack-sourced runs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent import run_config
from agent.incidents import channels, service, tools
from agent.incidents.models import Incident, IncidentPolicy

CHANNEL = {
    "id": "C7",
    "name": "payments-oncall",
    "is_channel": True,
    "is_private": False,
    "is_im": False,
    "is_mpim": False,
    "is_ext_shared": False,
    "is_pending_ext_shared": False,
    "is_member": True,
}


def use_config(monkeypatch, **configurable):
    config = {
        "configurable": {
            "thread_id": "t-1",
            "source": "slack",
            "github_login": "sre",
            "slack_thread": {"channel_id": "C7", "thread_ts": "3.0", "triggering_user_id": "U1"},
            **configurable,
        }
    }
    monkeypatch.setattr(run_config, "get_config", lambda: config)


async def fake_apply(record: Incident, action: str, actor, *, keep_run_id: str = "") -> Incident:
    record.status = {"pause": "paused", "complete": "completed"}.get(action, "watching")
    await service.INCIDENTS.put(record.id, record)
    return record


@pytest.fixture
async def configured(fake_store, monkeypatch):
    await service.POLICIES.put(
        "default",
        IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1", channel_prefix="inc-"),
    )
    use_config(monkeypatch)
    monkeypatch.setattr(tools, "get_slack_channel_info", AsyncMock(return_value=dict(CHANNEL)))
    monkeypatch.setattr(tools, "current_run_id", lambda: "run-now")
    monkeypatch.setattr(
        tools, "dashboard_incident_url", lambda incident_id: f"https://dash/incidents/{incident_id}"
    )
    record = Incident(
        id=service.incident_id("T1", "C7"),
        workspace_id="T1",
        channel_id="C7",
        channel_name="payments-oncall",
        thread_id="thread-7",
    )
    monkeypatch.setattr(channels, "enroll_channel", AsyncMock(return_value=record))
    monkeypatch.setattr(channels, "apply_control", AsyncMock(side_effect=fake_apply))
    return SimpleNamespace(record=record)


async def test_start_follows_the_current_channel_without_the_prefix(configured):
    result = await tools.manage_incident("start")

    assert result["success"] is True and result["changed"] is True
    assert result["incident_id"] == configured.record.id
    assert result["dashboard_url"].endswith(configured.record.id)
    assert "notified" in result["note"]
    channels.enroll_channel.assert_awaited_once()
    args, kwargs = channels.enroll_channel.await_args
    assert args[:2] == ("C7", "payments-oncall") and kwargs == {"manual": True}


async def test_start_needs_a_connected_account_and_an_eligible_channel(configured, monkeypatch):
    use_config(monkeypatch, github_login=None)
    denied = await tools.manage_incident("start")
    assert denied["success"] is False and "connected Open SWE account" in denied["error"]

    use_config(monkeypatch)
    tools.get_slack_channel_info.return_value = {**CHANNEL, "is_private": True}
    private = await tools.manage_incident("start")
    assert private["success"] is False and "public internal" in private["error"]

    tools.get_slack_channel_info.return_value = dict(CHANNEL)
    policy = await service.get_policy()
    await service.POLICIES.put(
        "default", policy.model_copy(update={"excluded_channel_ids": ["C7"]})
    )
    excluded = await tools.manage_incident("start")
    assert excluded["success"] is False
    channels.enroll_channel.assert_not_awaited()


async def test_start_reports_setup_failures_and_repeated_starts(configured):
    configured.record.status = "needs_attention"
    failed = await tools.manage_incident("start")
    assert failed["success"] is False and "setup failed" in failed["error"]

    configured.record.status = "watching"
    await service.INCIDENTS.put(configured.record.id, configured.record)
    again = await tools.manage_incident("start")
    assert again == {
        **again,
        "success": True,
        "changed": False,
        "status": "watching",
    }
    channels.apply_control.assert_not_awaited()

    configured.record.status = "completed"
    await service.INCIDENTS.put(configured.record.id, configured.record)
    reopened = await tools.manage_incident("start")
    assert reopened["changed"] is True and reopened["status"] == "watching"
    assert channels.apply_control.await_args.args[1] == "reopen"


async def test_controls_change_the_current_incident_and_keep_this_run(configured):
    record = configured.record
    await service.INCIDENTS.put(record.id, record)

    paused = await tools.manage_incident("pause")
    assert (paused["changed"], paused["status"]) == (True, "paused")
    args, kwargs = channels.apply_control.await_args
    assert args[1] == "pause"
    assert args[2] == {"id": "slack:U1", "platform": "slack"}
    assert kwargs == {"keep_run_id": "run-now"}

    unchanged = await tools.manage_incident("pause")
    assert (unchanged["changed"], unchanged["status"]) == (False, "paused")
    assert channels.apply_control.await_count == 1

    completed = await tools.manage_incident("complete")
    assert completed["status"] == "completed"
    resumed = await tools.manage_incident("resume")
    assert resumed["status"] == "watching"
    assert [call.args[1] for call in channels.apply_control.await_args_list] == [
        "pause",
        "complete",
        "reopen",
    ]


async def test_controls_need_an_incident_channel_and_an_enabled_policy(configured, monkeypatch):
    missing = await tools.manage_incident("pause")
    assert missing["success"] is False and "not an incident" in missing["error"]

    configured.record.is_archived = True
    await service.INCIDENTS.put(configured.record.id, configured.record)
    archived = await tools.manage_incident("complete")
    assert archived["success"] is False and "archived" in archived["error"]

    policy = await service.get_policy()
    await service.POLICIES.put("default", policy.model_copy(update={"enabled": False}))
    disabled = await tools.manage_incident("start")
    assert disabled["success"] is False and "not enabled" in disabled["error"]

    use_config(monkeypatch, slack_thread=None)
    nowhere = await tools.manage_incident("start")
    assert nowhere["success"] is False and "Slack channel" in nowhere["error"]
    channels.apply_control.assert_not_awaited()


async def test_a_run_cannot_reopen_the_incident_it_just_completed(configured):
    """The run that completed an incident must not undo it seconds later."""
    record = configured.record
    record.status, record.completed_run_id = "completed", "run-now"
    await service.INCIDENTS.put(record.id, record)

    for action in ("resume", "start"):
        refused = await tools.manage_incident(action)
        assert refused["success"] is False
        assert "completed" in refused["error"]
    channels.apply_control.assert_not_awaited()


async def test_reopening_a_dashboard_completed_incident_is_still_allowed(configured, monkeypatch):
    """No run closed it and none is running, so two empty run ids must not read as a match."""
    record = configured.record
    record.status, record.completed_run_id = "completed", ""
    await service.INCIDENTS.put(record.id, record)
    monkeypatch.setattr(tools, "current_run_id", lambda: "")

    reopened = await tools.manage_incident("resume")

    assert reopened["success"] is True and reopened["status"] == "watching"
