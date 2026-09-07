from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.investigations import service
from agent.investigations.models import Investigation, InvestigationPolicy


@pytest.fixture
async def configured(fake_store, monkeypatch):
    await service.POLICIES.put(
        "default",
        InvestigationPolicy(
            enabled=True,
            workspace_id="T1",
            slack_app_id="A1",
            enabled_at=100,
        ),
    )
    monkeypatch.setattr(service, "_wake", AsyncMock())
    return fake_store


def event(kind="channel_created", channel=None, event_id="E1"):
    return {
        "type": "event_callback",
        "team_id": "T1",
        "api_app_id": "A1",
        "event_id": event_id,
        "event_time": 200,
        "event": {"type": kind, "channel": channel or {"id": "C1", "name": "inc-api"}},
    }


async def test_creation_is_durable_before_dispatch_and_duplicate_is_not_reapplied(configured):
    assert await service.accept_slack_event(event()) == {"status": "accepted"}
    assert len(await service.RECEIPTS.search_all()) == 1
    assert await service.accept_slack_event(event()) == {"status": "duplicate"}
    assert len(await service.RECEIPTS.search_all()) == 1


async def test_workspace_binding_and_old_event_are_not_enrollment(configured):
    wrong = event()
    wrong["team_id"] = "T2"
    with pytest.raises(HTTPException) as error:
        await service.accept_slack_event(wrong)
    assert error.value.status_code == 401
    old = event()
    old["event_time"] = 50
    assert await service.accept_slack_event(old) == {"status": "ignored"}
    assert not await service.RECEIPTS.search_all()


async def test_unregistered_messages_do_not_enroll_but_known_channels_never_fall_through(
    configured,
):
    assert await service.accept_slack_event(event("message", "C1")) is None
    await service.INVESTIGATIONS.put(
        service.investigation_id("T1", "C1"),
        Investigation(
            id=service.investigation_id("T1", "C1"),
            workspace_id="T1",
            channel_id="C1",
            thread_id="thread",
        ),
    )
    policy = await service.POLICIES.get("default")
    policy.enabled = False
    await service.POLICIES.put("default", policy)
    assert await service.accept_slack_event(event("message", "C1")) is not None


async def test_receipt_storage_failure_is_retryable_not_acknowledged(configured, monkeypatch):
    monkeypatch.setattr(service.RECEIPTS, "put", AsyncMock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        await service.accept_slack_event(event())


async def test_dispatch_failure_preserves_accepted_receipt_for_recovery(configured, monkeypatch):
    monkeypatch.setattr(service, "_wake", AsyncMock(side_effect=RuntimeError("offline")))
    assert await service.accept_slack_event(event()) == {"status": "accepted"}
    assert len(await service.RECEIPTS.search_all()) == 1


async def test_changed_body_under_same_event_id_is_rejected(configured):
    await service.accept_slack_event(event())
    with pytest.raises(HTTPException) as error:
        await service.accept_slack_event(event(channel={"id": "C2", "name": "inc-other"}))
    assert error.value.status_code == 409


async def test_messages_after_accepted_creation_route_before_registration(configured):
    await service.accept_slack_event(event())
    assert await service.accept_slack_event(event("message", "C1", "E2")) == {"status": "accepted"}
    assert len(await service.RECEIPTS.search_all()) == 2


async def test_invalid_policy_is_a_client_error(configured):
    with pytest.raises(HTTPException) as error:
        await service.update_settings({"channel_prefix": "*"}, 0, {"id": "github:admin"})
    assert error.value.status_code == 422


async def test_slack_receipts_bound_and_redact_persisted_context(configured):
    await service.accept_slack_event(event())
    message = event("message", "C1", "E2")
    message["event"]["text"] = "password=not-a-real-password"
    await service.accept_slack_event(message)
    receipts = await service.RECEIPTS.search_all()
    assert "not-a-real-password" not in str([r.payload for r in receipts])
    message["event_id"] = "oversized"
    message["event"]["text"] = "x" * (256 * 1024)
    with pytest.raises(HTTPException) as error:
        await service.accept_slack_event(message)
    assert error.value.status_code == 413


async def test_admin_sees_redacted_setup_failure_but_responder_cannot(configured, monkeypatch):
    record = Investigation(
        id="failed",
        workspace_id="T1",
        channel_id="C1",
        thread_id="thread",
        status="needs_attention",
        reason="slack_unavailable",
        channel_name="sensitive-title",
        title="sensitive-title",
    )
    await service.INVESTIGATIONS.put(record.id, record)
    monkeypatch.setattr(
        service.slack, "channel_info", AsyncMock(side_effect=RuntimeError("offline"))
    )
    assert (await service.list_investigations())["items"] == []
    items = (await service.list_investigations(include_setup=True))["items"]
    assert items[0]["title"] == "Channel setup needs attention"
    detail = await service.get_investigation(record.id, include_setup=True)
    assert detail["report"] is None
    assert detail["activity"] == []
    assert detail["allowed_actions"] == []


async def test_command_retries_remain_idempotent_after_state_change(configured):
    import time

    record = Investigation(
        id="command",
        workspace_id="T1",
        channel_id="C1",
        thread_id="thread",
        status="watching",
        can_read=True,
        last_verified_at=time.time(),
    )
    await service.INVESTIGATIONS.put(record.id, record)
    actor = {"id": "github:responder"}
    await service.submit_command(record.id, "pause", None, "request", actor)
    record.status = "paused"
    await service.INVESTIGATIONS.put(record.id, record)
    assert (await service.submit_command(record.id, "pause", None, "request", actor))[
        "status"
    ] == "duplicate"


def _auth(team_id="T1"):
    return AsyncMock(
        return_value={"team_id": team_id, "granted_scopes": sorted(service.REQUIRED_SLACK_SCOPES)}
    )


async def test_enabling_binds_identity_from_installation_and_env(fake_store, monkeypatch):
    monkeypatch.setattr(service, "_wake", AsyncMock())
    monkeypatch.setattr(service, "ensure_recovery", AsyncMock())
    monkeypatch.setattr(service.slack, "request", _auth("T9"))
    monkeypatch.setenv("SLACK_APP_ID", "A9")
    result = await service.update_settings(
        {"enabled": True, "workspace_id": "TX", "slack_app_id": "AX"}, 0, {"id": "github:admin"}
    )
    assert result["status"] == "accepted"
    policy = (await service.RECEIPTS.search_all())[0].payload["policy"]
    assert (policy["workspace_id"], policy["slack_app_id"]) == ("T9", "A9")


async def test_enabling_requires_slack_app_id(fake_store, monkeypatch):
    monkeypatch.setattr(service, "_wake", AsyncMock())
    monkeypatch.setattr(service.slack, "request", _auth())
    monkeypatch.delenv("SLACK_APP_ID", raising=False)
    with pytest.raises(HTTPException) as error:
        await service.update_settings({"enabled": True}, 0, {"id": "github:admin"})
    assert error.value.status_code == 422
    assert "SLACK_APP_ID" in error.value.detail


async def test_identity_cannot_change_with_registered_investigations(configured, monkeypatch):
    monkeypatch.setattr(service, "ensure_recovery", AsyncMock())
    await service.INVESTIGATIONS.put(
        service.investigation_id("T1", "C1"),
        Investigation(
            id=service.investigation_id("T1", "C1"),
            workspace_id="T1",
            channel_id="C1",
            thread_id="thread",
        ),
    )
    monkeypatch.setattr(service.slack, "request", _auth("T1"))
    monkeypatch.setenv("SLACK_APP_ID", "A2")
    with pytest.raises(HTTPException) as error:
        await service.update_settings({"enabled": True}, 0, {"id": "github:admin"})
    assert error.value.status_code == 409
    monkeypatch.setenv("SLACK_APP_ID", "A1")
    result = await service.update_settings({"enabled": True}, 0, {"id": "github:admin"})
    assert result["status"] == "accepted"


async def test_disabling_preserves_binding_without_slack_call(configured, monkeypatch):
    request = AsyncMock()
    monkeypatch.setattr(service.slack, "request", request)
    await service.update_settings(
        {"enabled": False, "workspace_id": "", "slack_app_id": ""}, 0, {"id": "github:admin"}
    )
    policy = (await service.RECEIPTS.search_all())[0].payload["policy"]
    assert (policy["workspace_id"], policy["slack_app_id"]) == ("T1", "A1")
    request.assert_not_awaited()


async def test_settings_report_server_owned_identity(configured, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_ID", "A1")
    monkeypatch.setattr(service.slack, "request", _auth("T1"))
    connection = (await service.get_settings())["connection"]
    assert (connection["workspace_id"], connection["slack_app_id"]) == ("T1", "A1")
    assert connection["required_scopes_present"] is True
    assert connection["error"] is None
    monkeypatch.delenv("SLACK_APP_ID")
    connection = (await service.get_settings())["connection"]
    assert "SLACK_APP_ID" in connection["error"]
    assert connection["slack_app_id"] == "A1"


async def test_detail_carries_the_langsmith_thread_link(configured, monkeypatch):
    import time

    record = Investigation(
        id="traced",
        workspace_id="T1",
        channel_id="C1",
        thread_id="thread-1",
        status="watching",
        can_read=True,
        last_verified_at=time.time(),
    )
    await service.INVESTIGATIONS.put(record.id, record)
    trace = AsyncMock(return_value="https://smith.langchain.com/o/t/projects/p/p1/t/thread-1")
    monkeypatch.setattr(service, "get_langsmith_trace_url", trace)
    detail = await service.get_investigation(record.id)
    assert detail["trace_url"] == "https://smith.langchain.com/o/t/projects/p/p1/t/thread-1"
    trace.assert_awaited_once_with("thread-1")
