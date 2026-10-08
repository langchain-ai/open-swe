import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from openswe.api.app import app
from openswe.github import webhook as github
from openswe.schedules import store as schedules
from openswe.webhooks import common
from tests.conftest import post_signed_github_webhook

_SECRET = "baby-sit-webhook-secret"


async def _post(
    event_type: str, payload: dict[str, Any], *, delivery_id: str = "delivery-1"
) -> httpx.Response:
    return await post_signed_github_webhook(
        event_type, payload, secret=_SECRET, delivery_id=delivery_id
    )


@pytest.mark.parametrize("event_type", ["check_run", "check_suite", "workflow_run", "status"])
async def test_signed_ci_events_route_without_mention(
    event_type: str, registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def process(payload: dict[str, Any], kind: str, delivery_id: str | None) -> None:
        captured.update({"payload": payload, "event_type": kind, "delivery_id": delivery_id})

    monkeypatch.setattr(common, "GITHUB_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(common, "is_repo_allowed", lambda _repo: True)
    monkeypatch.setattr(github, "process_github_ci_event", process)
    payload = {"repository": {"owner": {"login": "acme"}, "name": "repo"}}

    response = await _post(event_type, payload)

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "message": "Processing GitHub CI event"}
    assert captured == {
        "payload": payload,
        "event_type": event_type,
        "delivery_id": "delivery-1",
    }


@pytest.mark.parametrize("action", ["completed", "requested", "in_progress"])
async def test_workflow_automation_dispatches_only_completed_runs(
    action: str, registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliveries: list[str] = []

    async def launch(event_type: str, payload: dict[str, object], delivery_id: str) -> list[object]:
        deliveries.append(delivery_id)
        return []

    async def process(*args: object) -> None:
        return None

    monkeypatch.setattr(common, "GITHUB_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(common, "is_repo_allowed", lambda _repo: True)
    monkeypatch.setattr(github, "process_github_ci_event", process)
    monkeypatch.setattr(schedules, "launch_github_automations", launch)
    response = await _post(
        "workflow_run",
        {
            "action": action,
            "repository": {"owner": {"login": "acme"}, "name": "repo"},
        },
    )
    assert response.status_code == 200
    assert deliveries == (["delivery-1"] if action == "completed" else [])


def test_ci_event_still_requires_valid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "GITHUB_WEBHOOK_SECRET", _SECRET)
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
