"""MDA's Linear channel asks the backend what each Linear event starts, and only it can."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from standardwebhooks import Webhook

from openswe.linear import channel

_SERVICE_SECRET = "5e" * 32  # noqa: S105 - test value
_THREAD = str(uuid.uuid4())


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    record = AsyncMock(return_value=True)
    monkeypatch.setattr("openswe.linear.channel.EventLog.record", record)
    automations = AsyncMock()
    monkeypatch.setattr(channel, "_launch_automations", automations)
    api = FastAPI()
    api.state.record = record
    api.state.automations = automations
    api.include_router(channel.router)
    return api


def _call(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """What MDA's channel POSTs its endpoint."""
    return {
        "event": {"key": key, "deliveryId": "delivery-1", "payload": payload},
        "thread": {"id": _THREAD, "channel": {"name": "linear"}},
    }


async def _post(
    app: FastAPI, body: dict[str, Any], secret: str | None = _SERVICE_SECRET
) -> httpx.Response:
    """POST ``body`` as MDA does: signed with ``secret``, or unsigned without one."""
    content = json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if secret is not None:
        timestamp = datetime.now(tz=UTC)
        headers |= {
            "webhook-id": "msg_delivery-1",
            "webhook-timestamp": str(int(timestamp.timestamp())),
            "webhook-signature": Webhook(secret).sign(
                "msg_delivery-1", timestamp, content.decode()
            ),
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/linear/channel/events", content=content, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", [None, "ab" * 32])
async def test_only_the_mda_deployment_may_call(app: FastAPI, secret: str | None) -> None:
    response = await _post(app, _call("Issue.create", {"type": "Issue"}), secret)
    assert response.status_code == 401
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_nobody_may_call_without_a_configured_secret(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MDA_SERVICE_SECRET")
    response = await _post(app, _call("Issue.create", {"type": "Issue"}))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unknown_events_and_oversized_payloads_are_refused(app: FastAPI) -> None:
    assert (await _post(app, _call("Issue.remove", {}))).status_code == 422
    huge = _call("Issue.create", {"type": "Issue", "data": {"description": "x" * 1_000_001}})
    assert (await _post(app, huge)).status_code == 422
    not_an_object = _call("Issue.create", {})
    not_an_object["event"]["payload"] = ["Issue"]
    assert (await _post(app, not_an_object)).status_code == 422
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_events_start_nothing_and_are_logged_and_run_automations(
    app: FastAPI,
) -> None:
    payload = {"type": "Issue", "action": "create", "actor": {"email": "ada@example.com"}}
    response = await _post(app, _call("Issue.create", payload))
    assert response.status_code == 200
    # The channel starts nothing for null.
    assert response.json() is None
    _request, body, source = app.state.record.await_args.args
    kwargs = app.state.record.await_args.kwargs
    assert (json.loads(body), source) == (payload, "linear")
    assert (kwargs["event_type"], kwargs["delivery_id"]) == ("Issue", "delivery-1")
    assert kwargs["refs"].email == "ada@example.com"
    automation_payload, delivery_id = app.state.automations.await_args.args
    assert (automation_payload, delivery_id) == (payload, "delivery-1")


@pytest.mark.asyncio
async def test_comments_are_logged_without_automations(app: FastAPI) -> None:
    response = await _post(app, _call("Comment.create", {"type": "Comment"}))
    assert response.json() is None
    app.state.record.assert_awaited_once()
    app.state.automations.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_session_turn_starts_the_run_prepared_on_the_sessions_thread(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = {
        "input": {"messages": []},
        "context": {"graph": "agent"},
        "metadata": {"channel": "linear"},
        "webhook": "https://backend.example/webhooks/run-complete?token=t",
    }
    prepare = AsyncMock(return_value=prepared)
    monkeypatch.setattr(channel.LinearSessionTurn, "prepare_run", prepare)
    payload = {
        "type": "AgentSessionEvent",
        "action": "created",
        "organizationId": "org-1",
        "webhookTimestamp": 1_700_000_000_000,
        "agentSession": {
            "id": "session-1",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "title": "Bug"},
            "comment": {"id": "comment-1", "body": "@Open SWE fix it"},
        },
    }

    response = await _post(app, _call("AgentSessionEvent.created", payload))

    assert response.status_code == 200
    # MDA's channel starts exactly this run, which completes through the webhook.
    assert response.json() == prepared
    (thread_id,) = prepare.await_args.args
    assert thread_id == _THREAD
    app.state.record.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_session_turn_the_backend_declines_starts_nothing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(channel.LinearSessionTurn, "prepare_run", AsyncMock(return_value=None))
    payload = {
        "type": "AgentSessionEvent",
        "action": "created",
        "organizationId": "org-1",
        "webhookTimestamp": 1_700_000_000_000,
        "agentSession": {"id": "session-1", "issue": {"id": "issue-1"}},
    }
    response = await _post(app, _call("AgentSessionEvent.created", payload))
    assert response.json() is None
