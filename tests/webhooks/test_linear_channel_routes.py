"""MDA's Linear channel hands the backend the Linear events it verified, and only it can."""

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openswe.linear import channel

_SERVICE_SECRET = "service-secret"  # noqa: S105 - test value
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


def _event(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"thread_id": _THREAD, "delivery_id": "delivery-1", "key": key, "payload": payload}


async def _post(
    app: FastAPI, path: str, body: dict[str, Any], secret: str | None = _SERVICE_SECRET
) -> httpx.Response:
    headers = {"x-mda-service-secret": secret} if secret is not None else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(f"/linear/channel{path}", json=body, headers=headers)


async def _forward(
    app: FastAPI, payload: dict[str, Any] | bytes, secret: str | None = _SERVICE_SECRET
) -> httpx.Response:
    """A delivery as the channel forwards it: as Linear sent it, with the secret added."""
    headers = {"linear-delivery": "delivery-1", "linear-signature": "signed-by-linear"}
    if secret is not None:
        headers["x-mda-service-secret"] = secret
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/linear/channel/events", content=body, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", [None, "guess"])
async def test_only_the_mda_deployment_may_call(app: FastAPI, secret: str | None) -> None:
    response = await _forward(app, {"type": "Issue", "action": "create"}, secret)
    assert response.status_code == 401
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_nobody_may_call_without_a_configured_secret(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MDA_SERVICE_SECRET")
    response = await _forward(app, {"type": "Issue", "action": "create"}, "")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_other_events_are_ignored_and_malformed_ones_refused(app: FastAPI) -> None:
    ignored = await _forward(app, {"type": "Issue", "action": "remove"})
    assert ignored.json() == {"status": "ignored"}
    assert (await _forward(app, b"[]")).status_code == 422
    huge = {"type": "Issue", "action": "create", "data": {"description": "x" * 1_000_001}}
    assert (await _forward(app, huge)).status_code == 413
    huge_turn = _event("AgentSessionEvent.created", huge)
    assert (await _post(app, "/runs", huge_turn)).status_code == 422
    app.state.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_events_are_logged_and_run_automations(app: FastAPI) -> None:
    payload = {"type": "Issue", "action": "create", "actor": {"email": "ada@example.com"}}
    response = await _forward(app, payload)
    assert response.json() == {"status": "accepted"}
    _request, body, source = app.state.record.await_args.args
    kwargs = app.state.record.await_args.kwargs
    # The event log keeps the delivery as Linear sent it.
    assert (json.loads(body), source) == (payload, "linear")
    assert (kwargs["event_type"], kwargs["delivery_id"]) == ("Issue", "delivery-1")
    assert kwargs["refs"].email == "ada@example.com"
    automation_payload, delivery_id = app.state.automations.await_args.args
    assert (automation_payload, delivery_id) == (payload, "delivery-1")


@pytest.mark.asyncio
async def test_comments_are_logged_without_automations(app: FastAPI) -> None:
    response = await _forward(app, {"type": "Comment", "action": "create"})
    assert response.status_code == 200
    app.state.record.assert_awaited_once()
    app.state.automations.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_event_that_is_no_session_turn_starts_no_run(app: FastAPI) -> None:
    response = await _post(app, "/runs", _event("Comment.create", {"type": "Comment"}))
    assert response.status_code == 200
    assert response.json() == {"run": None}


@pytest.mark.asyncio
async def test_a_session_turn_is_prepared_on_the_sessions_thread(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = {
        "input": {"messages": []},
        "context": {"graph": "agent"},
        "metadata": {"channel": "linear"},
    }
    prepare = AsyncMock(return_value=prepared)
    monkeypatch.setattr(channel.LinearSessionTurn, "prepare_run", prepare)
    payload = {
        "type": "AgentSessionEvent",
        "action": "created",
        "agentSession": {
            "id": "session-1",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "title": "Bug"},
            "comment": {"id": "comment-1", "body": "@Open SWE fix it"},
        },
    }

    response = await _post(app, "/runs", _event("AgentSessionEvent.created", payload))

    assert response.status_code == 200
    assert response.json() == {"run": prepared}
    (thread_id,) = prepare.await_args.args
    assert thread_id == _THREAD
    turn = channel.LinearSessionTurn.parse("AgentSessionEvent.created", payload)
    assert turn is not None
    assert (turn.session_id, turn.issue.id, turn.body) == (
        "session-1",
        "issue-1",
        "@Open SWE fix it",
    )
