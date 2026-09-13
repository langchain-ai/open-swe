"""Incident settings, dashboard projections, and responder commands."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.incidents import channels, service, turns
from agent.incidents.models import Incident, IncidentPolicy, IncidentReport, IncidentReportRecord

CHANNEL = {
    "id": "C1",
    "name": "inc-api",
    "is_channel": True,
    "is_member": True,
    "is_private": False,
    "is_ext_shared": False,
    "is_pending_ext_shared": False,
}


@pytest.fixture
async def configured(fake_store, monkeypatch):
    await service.POLICIES.put(
        "default",
        IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1", enabled_at=100),
    )
    monkeypatch.setattr(service, "get_slack_channel_info", AsyncMock(return_value=dict(CHANNEL)))
    monkeypatch.setattr(turns, "has_active_run", AsyncMock(return_value=False))
    monkeypatch.setattr(
        service, "get_langsmith_trace_url", AsyncMock(return_value="https://smith/t")
    )
    return fake_store


def _auth(monkeypatch, team="T1", scopes=None):
    monkeypatch.setattr(
        service,
        "_auth_test",
        AsyncMock(
            return_value=(
                {"team_id": team},
                list(service.REQUIRED_SLACK_SCOPES) if scopes is None else scopes,
            )
        ),
    )
    return service._auth_test


async def _record(status="watching", **fields) -> Incident:
    record = Incident(
        id=service.incident_id("T1", fields.pop("channel_id", "C1")),
        workspace_id="T1",
        channel_id=fields.pop("channel", "C1"),
        channel_name="inc-api",
        thread_id="thread-1",
        status=status,
        **fields,
    )
    await service.INCIDENTS.put(record.id, record)
    return record


async def test_enabling_binds_identity_from_installation_and_env(fake_store, monkeypatch):
    _auth(monkeypatch, "T9")
    monkeypatch.setenv("SLACK_APP_ID", "A9")
    result = await service.update_settings(
        {"enabled": True, "workspace_id": "TX", "slack_app_id": "AX"}, 0, {"id": "github:admin"}
    )
    policy = await service.get_policy()
    assert result == {"command_id": "settings:1", "status": "applied"}
    assert (policy.workspace_id, policy.slack_app_id, policy.version) == ("T9", "A9", 1)
    assert policy.enabled_at > 0


async def test_enabling_requires_slack_app_id(fake_store, monkeypatch):
    _auth(monkeypatch)
    monkeypatch.delenv("SLACK_APP_ID", raising=False)
    with pytest.raises(HTTPException) as error:
        await service.update_settings({"enabled": True}, 0, {"id": "github:admin"})
    assert error.value.status_code == 422
    assert "SLACK_APP_ID" in error.value.detail


async def test_identity_cannot_change_with_registered_incidents(configured, monkeypatch):
    await _record()
    _auth(monkeypatch, "T1")
    monkeypatch.setenv("SLACK_APP_ID", "A2")
    with pytest.raises(HTTPException) as error:
        await service.update_settings({"enabled": True}, 0, {"id": "github:admin"})
    assert error.value.status_code == 409
    monkeypatch.setenv("SLACK_APP_ID", "A1")
    assert (await service.update_settings({"enabled": True}, 0, {"id": "github:admin"}))[
        "status"
    ] == "applied"


async def test_disabling_preserves_binding_without_slack_call(configured, monkeypatch):
    auth = _auth(monkeypatch)
    await service.update_settings(
        {"enabled": False, "workspace_id": "", "slack_app_id": ""}, 0, {"id": "github:admin"}
    )
    policy = await service.get_policy()
    assert (policy.workspace_id, policy.slack_app_id, policy.enabled) == ("T1", "A1", False)
    auth.assert_not_awaited()


@pytest.mark.parametrize(
    ("body", "version", "status"),
    [
        ({"enabled": False}, 3, 409),
        ({"channel_prefix": "*"}, 0, 422),
        ({"model": "nope:x"}, 0, 422),
    ],
)
async def test_invalid_settings_are_client_errors(configured, monkeypatch, body, version, status):
    _auth(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await service.update_settings(body, version, {"id": "github:admin"})
    assert error.value.status_code == status


async def test_settings_report_server_owned_identity(configured, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_ID", "A1")
    _auth(monkeypatch, "T1")
    settings = await service.get_settings()
    connection = settings["connection"]
    assert (connection["workspace_id"], connection["slack_app_id"]) == ("T1", "A1")
    assert connection["required_scopes_present"] is True
    assert connection["error"] is None
    assert settings["last_operation"]["command_id"] == "settings:0"
    monkeypatch.delenv("SLACK_APP_ID")
    connection = (await service.get_settings())["connection"]
    assert "SLACK_APP_ID" in connection["error"]
    assert connection["slack_app_id"] == "A1"


async def test_list_filters_by_activity_and_hides_unreadable_channels(configured, monkeypatch):
    watching = await _record()
    paused = await _record(status="paused", channel_id="C2", channel="C2")
    failed = await _record(
        status="needs_attention", channel_id="C3", channel="C3", reason="setup_failed"
    )
    await service.REPORTS.put(
        watching.id,
        IncidentReportRecord(
            incident_id=watching.id, report=IncidentReport(summary="Latest"), digest="d"
        ),
    )

    def info(channel_id: str, *, use_cache: bool = True):
        return None if channel_id == "C3" else {**CHANNEL, "id": channel_id}

    service.get_slack_channel_info.side_effect = info

    active = await service.list_incidents(view="active")
    inactive = await service.list_incidents(view="inactive")
    everything = await service.list_incidents(view="all", include_setup=True)

    assert [item["id"] for item in active["items"]] == [watching.id]
    assert active["items"][0]["latest_finding"] == "Latest"
    assert [item["id"] for item in inactive["items"]] == [paused.id]
    assert {item["id"] for item in everything["items"]} == {watching.id, paused.id, failed.id}
    assert next(item for item in everything["items"] if item["id"] == failed.id)[
        "title"
    ].startswith("Channel setup")
    assert (await service.list_incidents(q="payments"))["items"] == []


async def test_detail_derives_investigating_from_live_runs(configured):
    record = await _record()
    detail = await service.get_incident(record.id)
    assert detail["incident"]["status"] == "watching"
    assert detail["allowed_actions"] == ["ask", "investigate_again", "pause", "complete"]
    assert detail["trace_url"] == "https://smith/t"

    turns.has_active_run.return_value = True
    assert (await service.get_incident(record.id))["incident"]["status"] == "investigating"

    record.status = "completed"
    await service.INCIDENTS.put(record.id, record)
    assert (await service.get_incident(record.id))["allowed_actions"] == ["ask", "reopen"]


async def test_detail_merges_control_and_finding_activity(configured):
    record = await _record()
    service.note(record, "control", "Incident paused.")
    await service.INCIDENTS.put(record.id, record)
    latest = IncidentReportRecord(
        incident_id=record.id, report=IncidentReport(summary="Found it"), digest="d"
    )
    service.note(latest, "findings", "Found it")
    await service.REPORTS.put(record.id, latest)

    detail = await service.get_incident(record.id)

    assert {item["type"] for item in detail["activity"]} == {"control", "findings"}
    assert detail["report"]["summary"] == "Found it"


async def test_revoked_channel_access_hides_the_incident(configured):
    record = await _record()
    service.get_slack_channel_info.return_value = {**CHANNEL, "is_member": False}
    with pytest.raises(HTTPException) as error:
        await service.get_incident(record.id)
    assert error.value.status_code == 404
    service.get_slack_channel_info.return_value = None
    with pytest.raises(HTTPException) as error:
        await service.get_incident(record.id)
    assert error.value.status_code == 503


async def test_questions_dispatch_explicit_turns_with_server_identity(configured, monkeypatch):
    record = await _record()
    dispatch = AsyncMock(return_value={"run_id": "r1"})
    monkeypatch.setattr(turns, "dispatch_turn", dispatch)

    result = await service.submit_command(
        record.id,
        "ask",
        "What changed?",
        "r1",
        {"id": "github:sre", "github_login": "sre", "email": "sre@x"},
    )

    assert result == {"command_id": "r1", "status": "accepted"}
    kwargs = dispatch.await_args.kwargs
    assert kwargs["request"] == "What changed?"
    assert (
        kwargs["requester"]["id"] == "github:sre" and kwargs["requester"]["github_login"] == "sre"
    )
    await service.submit_command(record.id, "investigate_again", None, "r2", {"id": "github:sre"})
    assert dispatch.await_args.kwargs == {} or "request" not in dispatch.await_args.kwargs


@pytest.mark.parametrize(
    ("action", "text", "status"), [("ask", "", 422), ("resume", None, 409), ("dance", None, 409)]
)
async def test_unavailable_commands_are_rejected(configured, action, text, status):
    record = await _record()
    with pytest.raises(HTTPException) as error:
        await service.submit_command(record.id, action, text, "r1", {"id": "github:sre"})
    assert error.value.status_code == status


async def test_controls_route_through_the_channel_handler(configured, monkeypatch):
    record = await _record()
    control = AsyncMock(return_value=record)
    monkeypatch.setattr(channels, "apply_control", control)

    await service.submit_command(record.id, "pause", None, "r1", {"id": "github:sre"})

    control.assert_awaited_once()
    assert control.await_args.args[1:] == ("pause", {"id": "github:sre"})
