import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent.api.app import app
from agent.webhooks import github

_SECRET = "baby-sit-webhook-secret"


def _post(event_type: str, payload: dict[str, Any], *, delivery_id: str = "delivery-1"):
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return TestClient(app).post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event_type,
            "X-GitHub-Delivery": delivery_id,
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )


@pytest.mark.parametrize("event_type", ["check_run", "check_suite", "workflow_run", "status"])
def test_signed_ci_events_route_without_mention(
    event_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def process(payload: dict[str, Any], kind: str, delivery_id: str | None) -> None:
        captured.update({"payload": payload, "event_type": kind, "delivery_id": delivery_id})

    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", _SECRET)
    monkeypatch.delenv("ALLOWED_GITHUB_ORGS", raising=False)
    monkeypatch.delenv("ALLOWED_GITHUB_REPOS", raising=False)
    monkeypatch.setattr(github, "process_github_ci_event", process)
    payload = {"repository": {"owner": {"login": "acme"}, "name": "repo"}}

    response = _post(event_type, payload)

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "message": "Processing GitHub CI event"}
    assert captured == {
        "payload": payload,
        "event_type": event_type,
        "delivery_id": "delivery-1",
    }


def test_ci_event_still_requires_valid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", _SECRET)
    payload = {"repository": {"owner": {"login": "acme"}, "name": "repo"}}
    body = json.dumps(payload).encode()

    response = TestClient(app).post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "check_run",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )

    assert response.status_code == 401
