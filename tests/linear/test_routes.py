"""Authentication, replay protection and dispatch for the Linear webhook route."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agent.api.app import app
from agent.linear import routes as linear_routes
from agent.linear import sessions

_SECRET = "linear-test-secret"


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(linear_routes, "LINEAR_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(linear_routes, "claim_delivery", AsyncMock(return_value=True))
    return TestClient(app)


def _post(client: TestClient, payload: Any, *, delivery: str | None = "delivery-1"):
    body = (
        payload.encode()
        if isinstance(payload, str)
        else json.dumps(payload, separators=(",", ":")).encode()
    )
    headers = {
        "Linear-Signature": hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest(),
        "Content-Type": "application/json",
    }
    if delivery is not None:
        headers["Linear-Delivery"] = delivery
    return client.post("/webhooks/linear", content=body, headers=headers)


def _session_event(action: str = "created") -> dict[str, Any]:
    return {
        "type": "AgentSessionEvent",
        "action": action,
        "webhookTimestamp": _now_ms(),
        "agentSession": {
            "id": "session-1",
            "status": "pending",
            "issue": {"id": "issue-1", "identifier": "OS-1", "title": "Fix the thing"},
        },
        "promptContext": "Issue OS-1",
    }


def _comment_event(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "comment-1",
        "body": "@open-swe please fix this",
        "issue": {"id": "issue-1"},
        "user": {"id": "user-1", "email": "zhen@example.com"},
    }
    data.update(overrides)
    return {
        "type": "Comment",
        "action": "create",
        "webhookTimestamp": _now_ms(),
        "data": data,
    }


def test_agent_session_created_schedules_start(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = AsyncMock()
    monkeypatch.setattr(sessions, "start_session", start)

    response = _post(client, _session_event())

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    start.assert_awaited_once()
    assert start.await_args is not None
    assert start.await_args.args[0].agent_session.id == "session-1"


def test_agent_session_prompted_schedules_continue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    resume = AsyncMock()
    monkeypatch.setattr(sessions, "continue_session", resume)

    assert _post(client, _session_event("prompted")).json() == {"status": "accepted"}
    resume.assert_awaited_once()


def test_missing_delivery_header_is_rejected(client: TestClient) -> None:
    assert _post(client, _session_event(), delivery=None).status_code == 401


def test_bad_signature_is_rejected(client: TestClient) -> None:
    body = json.dumps(_session_event()).encode()
    response = client.post(
        "/webhooks/linear",
        content=body,
        headers={"Linear-Signature": "nope", "Linear-Delivery": "delivery-1"},
    )
    assert response.status_code == 401


def test_stale_delivery_is_rejected(client: TestClient) -> None:
    payload = _session_event()
    payload["webhookTimestamp"] = _now_ms() - 10 * 60 * 1000

    assert _post(client, payload).status_code == 401


def test_duplicate_delivery_is_ignored(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linear_routes, "claim_delivery", AsyncMock(return_value=False))
    start = AsyncMock()
    monkeypatch.setattr(sessions, "start_session", start)

    response = _post(client, _session_event())

    assert response.json()["status"] == "ignored"
    start.assert_not_called()


def test_malformed_json_is_rejected(client: TestClient) -> None:
    assert _post(client, "{not json").status_code == 400


def test_comment_from_our_own_app_user_is_ignored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    linear = AsyncMock()
    linear.viewer_id.return_value = "user-1"
    monkeypatch.setattr(sessions, "linear_client", lambda: linear)
    process = AsyncMock()
    monkeypatch.setattr(linear_routes, "process_linear_issue", process)

    response = _post(client, _comment_event())

    assert response.json()["status"] == "ignored"
    process.assert_not_called()


def test_comment_mention_schedules_processing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    linear = AsyncMock()
    linear.viewer_id.return_value = "app-user"
    monkeypatch.setattr(sessions, "linear_client", lambda: linear)
    monkeypatch.setattr(sessions, "load_issue", AsyncMock(side_effect=lambda issue: issue))
    monkeypatch.setattr(
        sessions,
        "resolve_repo_config",
        AsyncMock(return_value={"owner": "langchain-ai", "name": "open-swe"}),
    )
    process = AsyncMock()
    monkeypatch.setattr(linear_routes, "process_linear_issue", process)

    response = _post(client, _comment_event())

    assert response.json() == {"status": "accepted"}
    process.assert_awaited_once()
    assert process.await_args is not None
    assert process.await_args.kwargs["trigger"].id == "comment-1"


def test_comment_without_a_mention_is_ignored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    linear = AsyncMock()
    linear.viewer_id.return_value = "app-user"
    monkeypatch.setattr(sessions, "linear_client", lambda: linear)
    process = AsyncMock()
    monkeypatch.setattr(linear_routes, "process_linear_issue", process)

    response = _post(client, _comment_event(body="just chatting"))

    assert response.json()["status"] == "ignored"
    process.assert_not_called()
