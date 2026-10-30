from typing import Any

import httpx
import pytest
from runtime_api.github import RUN_GITHUB_WORK

from openswe.api.app import app
from openswe.github import webhook as github
from openswe.webhooks import common
from tests.github_channel import SERVICE_SECRET, deliver_github_event


@pytest.mark.parametrize("event_type", ["check_run", "check_suite", "workflow_run", "status"])
async def test_ci_events_route_without_mention(
    event_type: str, registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def process(payload: dict[str, Any], kind: str, delivery_id: str | None) -> None:
        captured.update({"payload": payload, "event_type": kind, "delivery_id": delivery_id})

    monkeypatch.setattr(common, "is_repo_allowed", lambda _repo: True)
    monkeypatch.setattr(github, "process_github_ci_event", process)
    payload = {"repository": {"owner": {"login": "acme"}, "name": "repo"}}

    delivered = await deliver_github_event(
        monkeypatch, event_type, payload, delivery_id="delivery-1"
    )

    assert delivered.work == ("ci_event",)
    assert captured == {
        "payload": payload,
        "event_type": event_type,
        "delivery_id": "delivery-1",
    }


async def test_ci_work_still_requires_mdas_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            RUN_GITHUB_WORK.path,
            json={
                "event": "check_run",
                "delivery_id": "delivery-1",
                "payload": {},
                "work": ["ci_event"],
            },
        )

    assert response.status_code == 401
