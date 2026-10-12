"""Incident settings, dashboard projections, and responder commands."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.incidents import service, turns
from openswe.incidents.models import Incident, IncidentPolicy, IncidentReport, IncidentReportRecord
from openswe.slack.channels import SlackChannel

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
    monkeypatch.setattr(SlackChannel, "fetch", AsyncMock(return_value=dict(CHANNEL)))
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

    SlackChannel.fetch.side_effect = info

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


async def test_revoked_channel_access_hides_the_incident(configured):
    record = await _record()
    SlackChannel.fetch.return_value = {**CHANNEL, "is_member": False}
    with pytest.raises(HTTPException) as error:
        await service.get_incident(record.id)
    assert error.value.status_code == 404
    SlackChannel.fetch.return_value = None
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


async def test_cursor_pages_do_not_repeat_when_an_incident_moves_forward(configured):
    first = await _record(channel_id="C1", channel="C1")
    second = await _record(channel_id="C2", channel="C2")
    third = await _record(channel_id="C3", channel="C3")
    for index, record in enumerate((first, second, third)):
        record.updated_at = f"2026-09-13T10:0{3 - index}:00+00:00"
        await service.INCIDENTS.put(record.id, record)

    page_one = await service.list_incidents(limit=1)
    assert [item["id"] for item in page_one["items"]] == [first.id]
    assert page_one["next_cursor"] == f"{first.updated_at}|{first.id}"

    third.updated_at = "2026-09-13T11:00:00+00:00"
    await service.INCIDENTS.put(third.id, third)
    page_two = await service.list_incidents(limit=1, cursor=page_one["next_cursor"])

    assert [item["id"] for item in page_two["items"]] == [second.id]
    assert page_two["next_cursor"] is None
    with pytest.raises(HTTPException) as error:
        await service.list_incidents(cursor="5")
    assert error.value.status_code == 422


async def test_retried_commands_are_not_repeated(configured, monkeypatch):
    record = await _record()
    dispatch = AsyncMock(return_value={"run_id": "r1"})
    monkeypatch.setattr(turns, "dispatch_turn", dispatch)
    actor = {"id": "github:sre", "github_login": "sre"}

    first = await service.submit_command(record.id, "ask", "What changed?", "r1", actor)
    again = await service.submit_command(record.id, "ask", "What changed?", "r1", actor)

    assert (first["status"], again["status"]) == ("accepted", "duplicate")
    dispatch.assert_awaited_once()
    with pytest.raises(HTTPException) as error:
        await service.submit_command(record.id, "ask", "Something else", "r1", actor)
    assert error.value.status_code == 409

    dispatch.side_effect = RuntimeError("platform down")
    with pytest.raises(RuntimeError):
        await service.submit_command(record.id, "ask", "Retry me", "r2", actor)
    dispatch.side_effect = None
    assert (await service.submit_command(record.id, "ask", "Retry me", "r2", actor))["status"] == (
        "accepted"
    )


def test_manual_starts_do_not_need_the_prefix_but_keep_the_other_rules():
    policy = IncidentPolicy(channel_prefix="inc-", excluded_channel_ids=["C9"])
    channel = {**CHANNEL, "id": "C7", "name": "payments-oncall"}
    assert service.channel_allowed(channel, policy) is False
    assert service.channel_allowed(channel, policy, require_prefix=False) is True
    assert service.channel_allowed({**channel, "id": "C9"}, policy, require_prefix=False) is False
    assert (
        service.channel_allowed({**channel, "is_private": True}, policy, require_prefix=False)
        is False
    )
