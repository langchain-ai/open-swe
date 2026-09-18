"""Slack signature and early Incidents routing boundaries."""

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent.incidents import channels
from agent.slack import routes
from agent.slack.payloads import SlackChannelContext
from agent.webhooks import common

_SIGNING_SECRET = "incidents-test-signing-secret"


@pytest.fixture
def handler(monkeypatch):
    handle = AsyncMock(return_value={"status": "accepted"})
    monkeypatch.setattr(channels, "handle_slack_event", handle)
    return handle


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
        {"type": "app_mention", "channel": "C1", "text": "Incidents again"},
    ],
)
def test_signed_incidents_event_precedes_coding_filters(client, handler, monkeypatch, event):
    context = AsyncMock(side_effect=AssertionError("coding channel lookup must not run"))
    claim = AsyncMock(side_effect=AssertionError("generic fail-open claim must not run"))
    monkeypatch.setattr(common, "resolve_slack_channel_context", context)
    monkeypatch.setattr(common, "claim_slack_event", claim)

    response = _post(client, _payload(event))

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    handler.assert_awaited_once()
    assert handler.await_args.args[0] == _payload(event)
    context.assert_not_awaited()
    claim.assert_not_awaited()


@pytest.mark.parametrize("status", ["ignored", "duplicate"])
def test_registered_channel_stays_isolated_when_handler_declines_work(
    client, handler, monkeypatch, status
):
    handler.return_value = {"status": status}
    monkeypatch.setattr(
        common,
        "resolve_slack_channel_context",
        AsyncMock(side_effect=AssertionError("coding path")),
    )

    response = _post(client, _payload({"type": "app_mention", "channel": "C1"}))

    assert response.status_code == 200
    assert response.json() == {"status": status}


@pytest.mark.parametrize("failure", ["invalid_signature", "expired_timestamp"])
def test_invalid_slack_auth_never_reaches_incidents(client, handler, failure):
    response = _post(
        client,
        _payload({"type": "channel_created", "channel": {"id": "C1", "name": "inc-errors"}}),
        signature="v0=invalid" if failure == "invalid_signature" else None,
        timestamp=str(int(time.time()) - 3600) if failure == "expired_timestamp" else None,
    )

    assert response.status_code == 401
    handler.assert_not_awaited()


def test_handler_errors_do_not_fall_through_to_coding(client, handler, monkeypatch):
    handler.side_effect = HTTPException(401, "wrong installation")
    monkeypatch.setattr(
        common,
        "resolve_slack_channel_context",
        AsyncMock(side_effect=AssertionError("coding path")),
    )

    response = _post(client, _payload({"type": "channel_created", "channel": {"id": "C1"}}))

    assert response.status_code == 401


def test_unregistered_event_falls_through_to_ordinary_slack(client, handler, monkeypatch):
    handler.return_value = None
    context = AsyncMock(return_value=SlackChannelContext(is_ext_shared=True))
    monkeypatch.setattr(common, "resolve_slack_channel_context", context)

    response = _post(client, _payload({"type": "message", "channel": "ordinary"}))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "Slack channel is not eligible"}
    context.assert_awaited_once_with("ordinary", use_cache=False)


def test_signed_channel_creation_enrolls_once_and_dedupes_retries(client, fake_store, monkeypatch):
    fake_store.seed(
        ("incidents", "policies"),
        "default",
        {"enabled": True, "workspace_id": "T1", "slack_app_id": "A1", "channel_prefix": "inc-"},
    )
    claimed: set[str] = set()

    async def claim(event_id: str, channel_id: str = "", event_ts: str = "") -> bool:
        if event_id in claimed:
            return False
        claimed.add(event_id)
        return True

    monkeypatch.setattr(channels, "claim_slack_event", claim)
    enroll = AsyncMock()
    monkeypatch.setattr(channels, "enroll_channel", enroll)
    monkeypatch.setattr(
        common,
        "resolve_slack_channel_context",
        AsyncMock(side_effect=AssertionError("external lookup")),
    )
    payload = _payload({"type": "channel_created", "channel": {"id": "C1", "name": "inc-errors"}})

    first = _post(client, payload)
    second = _post(client, payload)

    assert first.status_code == 200
    assert first.json() == {"status": "accepted"}
    assert second.json() == {"status": "duplicate"}
    enroll.assert_awaited_once()
    assert enroll.await_args.args[:2] == ("C1", "inc-errors")


@pytest.mark.parametrize("field", ["team_id", "api_app_id"])
def test_signed_wrong_installation_cannot_enroll(client, fake_store, monkeypatch, field):
    fake_store.seed(
        ("incidents", "policies"),
        "default",
        {"enabled": True, "workspace_id": "T1", "slack_app_id": "A1"},
    )
    enroll = AsyncMock()
    monkeypatch.setattr(channels, "enroll_channel", enroll)
    monkeypatch.setattr(channels, "claim_slack_event", AsyncMock(return_value=True))
    payload = _payload({"type": "channel_created", "channel": {"id": "C1", "name": "inc-errors"}})
    payload[field] = "wrong-installation"

    response = _post(client, payload)

    assert response.status_code == 401
    enroll.assert_not_awaited()


def test_disabled_registered_channel_still_cannot_launch_coding(client, fake_store, monkeypatch):
    from agent.incidents import service

    fake_store.seed(
        ("incidents", "policies"),
        "default",
        {"enabled": False, "workspace_id": "T1", "slack_app_id": "A1"},
    )
    incident_id = service.incident_id("T1", "C1")
    fake_store.seed(
        ("incidents", "incidents"),
        incident_id,
        {"id": incident_id, "workspace_id": "T1", "channel_id": "C1", "thread_id": "i1"},
    )
    monkeypatch.setattr(channels, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(
        common,
        "resolve_slack_channel_context",
        AsyncMock(side_effect=AssertionError("coding lookup")),
    )

    response = _post(
        client, _payload({"type": "app_mention", "channel": "C1", "text": "<@BOT> fix this"})
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
