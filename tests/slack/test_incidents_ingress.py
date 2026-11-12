"""Slack signature and early Incidents routing boundaries."""

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.incidents import channels, service
from openswe.incidents.models import IncidentPolicy
from openswe.slack import routes
from openswe.slack.payloads import SlackChannelContext
from openswe.webhooks import common

pytestmark = pytest.mark.usefixtures("registry_db")


_SIGNING_SECRET = "incidents-test-signing-secret"


@pytest.fixture
def handler(monkeypatch):
    handle = AsyncMock(return_value={"status": "accepted"})
    monkeypatch.setattr(channels, "handle_slack_event", handle)
    return handle


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(channels.SlackChannel, "observe", AsyncMock())
    monkeypatch.setattr(common, "SLACK_SIGNING_SECRET", _SIGNING_SECRET)
    app = FastAPI()
    app.include_router(routes.router)
    from openswe.slack.events import reset_slack_event_claims

    reset_slack_event_claims()
    return TestClient(app)


def _payload(event: dict) -> dict:
    return {
        "type": "event_callback",
        "team_id": "T1",
        "api_app_id": "A1",
        "event_id": "Ev1",
        "event": event,
    }


def _incident_state(monkeypatch, policy: IncidentPolicy) -> None:
    monkeypatch.setattr(service.POLICIES, "get", AsyncMock(return_value=policy))


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


@pytest.mark.parametrize("status", ["duplicate"])
def test_an_incident_channel_event_stays_isolated_when_handler_declines_work(
    client, handler, monkeypatch, status
):
    handler.return_value = {"status": status}
    monkeypatch.setattr(
        common,
        "resolve_slack_channel_context",
        AsyncMock(side_effect=AssertionError("coding path")),
    )

    response = _post(
        client,
        _payload({"type": "channel_created", "channel": {"id": "C1", "name": "inc-payments"}}),
    )

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


async def test_overlapping_channel_events_queue_one_join(client, monkeypatch):
    _incident_state(
        monkeypatch,
        IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1", channel_prefix="inc-"),
    )
    monkeypatch.setattr(
        common,
        "resolve_slack_channel_context",
        AsyncMock(side_effect=AssertionError("external lookup")),
    )
    created = _payload({"type": "channel_created", "channel": {"id": "C1", "name": "inc-errors"}})
    renamed = {
        **_payload({"type": "channel_rename", "channel": {"id": "C1", "name": "inc-errors-2"}}),
        "event_id": "Ev2",
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app), base_url="http://test"
    ) as http:
        responses = [await _post(http, payload) for payload in (created, renamed, created)]

    assert [r.json() for r in responses] == [{"status": "accepted"}] * 3
    async with transaction() as conn:
        queued = await conn.scalar(
            text("SELECT count(*) FROM job WHERE kind = :kind"), {"kind": channels.JOIN_JOB}
        )
    assert queued == 1
