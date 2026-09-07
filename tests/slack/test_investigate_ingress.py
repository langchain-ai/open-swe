"""Slack signature and early Investigate routing boundaries."""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent import investigations, scheduler
from agent.slack import routes
from agent.webhooks import common

_SIGNING_SECRET = "investigate-test-signing-secret"


@pytest.fixture
def service(monkeypatch):
    service = SimpleNamespace(
        accept_slack_event=AsyncMock(return_value={"status": "accepted"}),
        recover=AsyncMock(return_value={"status": "recovered"}),
    )
    monkeypatch.setattr(investigations, "service", service, raising=False)
    return service


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(common, "SLACK_SIGNING_SECRET", _SIGNING_SECRET)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _payload(event: dict) -> dict:
    return {
        "type": "event_callback",
        "team_id": "T1",
        "api_app_id": "A1",
        "event_id": "Ev1",
        "event": event,
    }


def _post(client, payload, *, timestamp=None, signature=None):
    body = json.dumps(payload).encode()
    timestamp = timestamp or str(int(time.time()))
    computed = hmac.new(
        _SIGNING_SECRET.encode(),
        b"v0:" + timestamp.encode() + b":" + body,
        hashlib.sha256,
    ).hexdigest()
    return client.post(
        "/webhooks/slack",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature or f"v0={computed}",
        },
    )


@pytest.mark.parametrize(
    "event",
    [
        {"type": "channel_created", "channel": {"id": "C1", "name": "inc-payments"}},
        {"type": "channel_rename", "channel": {"id": "C1", "name": "inc-payments"}},
        {"type": "channel_archive", "channel": "C1"},
        {"type": "message", "channel": "C1", "subtype": "bot_message", "text": "Incident"},
        {"type": "message", "channel": "C1", "subtype": "message_deleted"},
        {"type": "app_mention", "channel": "C1", "text": "Investigate again"},
    ],
)
def test_signed_investigate_event_precedes_coding_filters(client, service, monkeypatch, event):
    context = AsyncMock(side_effect=AssertionError("coding channel lookup must not run"))
    claim = AsyncMock(side_effect=AssertionError("generic fail-open claim must not run"))
    monkeypatch.setattr(common, "_get_slack_channel_context", context)
    monkeypatch.setattr(common, "claim_slack_event", claim)

    response = _post(client, _payload(event))

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    service.accept_slack_event.assert_awaited_once_with(_payload(event))
    context.assert_not_awaited()
    claim.assert_not_awaited()


@pytest.mark.parametrize("status", ["ignored", "duplicate"])
def test_registered_channel_stays_isolated_when_service_declines_work(
    client, service, monkeypatch, status
):
    service.accept_slack_event.return_value = {"status": status}
    monkeypatch.setattr(
        common, "_get_slack_channel_context", AsyncMock(side_effect=AssertionError("coding path"))
    )

    response = _post(client, _payload({"type": "app_mention", "channel": "C1"}))

    assert response.status_code == 200
    assert response.json() == {"status": status}


@pytest.mark.parametrize("failure", ["invalid_signature", "expired_timestamp"])
def test_invalid_slack_auth_cannot_accept_investigate_receipts(client, service, failure):
    response = _post(
        client,
        _payload({"type": "channel_created", "channel": {"id": "C1", "name": "inc-errors"}}),
        signature="v0=invalid" if failure == "invalid_signature" else None,
        timestamp=str(int(time.time()) - 3600) if failure == "expired_timestamp" else None,
    )

    assert response.status_code == 401
    service.accept_slack_event.assert_not_awaited()


def test_acceptance_failure_is_retryable_and_does_not_fall_through(client, service, monkeypatch):
    service.accept_slack_event.side_effect = HTTPException(503, "durable inbox unavailable")
    monkeypatch.setattr(
        common, "_get_slack_channel_context", AsyncMock(side_effect=AssertionError("coding path"))
    )

    response = _post(client, _payload({"type": "channel_created", "channel": {"id": "C1"}}))

    assert response.status_code == 503


def test_unregistered_event_falls_through_to_ordinary_slack(client, service, monkeypatch):
    service.accept_slack_event.return_value = None
    context = AsyncMock(return_value={"is_ext_shared": True})
    monkeypatch.setattr(common, "_get_slack_channel_context", context)

    response = _post(client, _payload({"type": "message", "channel": "ordinary"}))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "Slack channel is not eligible"}
    context.assert_awaited_once_with("ordinary", use_cache=False)


async def test_investigate_scheduler_tick_recovers_instead_of_starting_coding_run(
    service, monkeypatch
):
    launch = AsyncMock(side_effect=AssertionError("coding schedule must not launch"))
    monkeypatch.setattr(scheduler, "launch_scheduled_agent_run", launch)

    result = await scheduler._launch(
        scheduler.SchedulerState(task="investigate", schedule_id="unrelated"), {}
    )

    assert result == {"result": {"status": "recovered"}}
    service.recover.assert_awaited_once_with()
    launch.assert_not_awaited()


def test_signed_channel_creation_persists_one_receipt_before_acknowledgment(
    client, fake_store, monkeypatch
) -> None:
    from agent.investigations import service

    fake_store.seed(
        ("investigate", "policies"),
        "default",
        {"enabled": True, "workspace_id": "T1", "slack_app_id": "A1", "channel_prefix": "inc-"},
    )
    monkeypatch.setattr(service, "wake", AsyncMock())
    monkeypatch.setattr(
        common,
        "_get_slack_channel_context",
        AsyncMock(side_effect=AssertionError("external lookup")),
    )
    payload = _payload({"type": "channel_created", "channel": {"id": "C1", "name": "inc-errors"}})

    first = _post(client, payload)
    second = _post(client, payload)

    assert first.status_code == 200
    assert first.json() == {"status": "accepted"}
    assert second.json() == {"status": "duplicate"}
    receipts = list(fake_store.values(("investigate", "receipts")).values())
    assert len(receipts) == 1
    assert receipts[0]["channel_id"] == "C1"
    assert receipts[0]["kind"] == "channel_created"
    assert receipts[0]["payload"] == payload["event"]


@pytest.mark.parametrize("field", ["team_id", "api_app_id"])
def test_signed_wrong_installation_cannot_persist_receipts(client, fake_store, monkeypatch, field):
    fake_store.seed(
        ("investigate", "policies"),
        "default",
        {"enabled": True, "workspace_id": "T1", "slack_app_id": "A1"},
    )
    monkeypatch.setattr(
        common,
        "_get_slack_channel_context",
        AsyncMock(side_effect=AssertionError("external lookup")),
    )
    payload = _payload({"type": "channel_created", "channel": {"id": "C1", "name": "inc-errors"}})
    payload[field] = "wrong-installation"

    response = _post(client, payload)

    assert response.status_code == 401
    assert fake_store.values(("investigate", "receipts")) == {}


def test_disabled_registered_channel_still_cannot_launch_coding(client, fake_store, monkeypatch):
    from agent.investigations import service

    fake_store.seed(
        ("investigate", "policies"),
        "default",
        {"enabled": False, "workspace_id": "T1", "slack_app_id": "A1"},
    )
    investigation_id = service.investigation_id("T1", "C1")
    fake_store.seed(
        ("investigate", "investigations"),
        investigation_id,
        {"id": investigation_id, "workspace_id": "T1", "channel_id": "C1", "thread_id": "i1"},
    )
    monkeypatch.setattr(
        common, "_get_slack_channel_context", AsyncMock(side_effect=AssertionError("coding lookup"))
    )

    response = _post(
        client, _payload({"type": "app_mention", "channel": "C1", "text": "<@BOT> fix this"})
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert fake_store.values(("investigate", "receipts")) == {}
