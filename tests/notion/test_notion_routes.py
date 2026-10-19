"""The /webhooks/notion endpoint: verification, signatures and event filtering."""

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openswe.notion import routes
from openswe.notion.models import WebhookEvent

SECRET = "secret_verification_token"


@pytest.fixture
def handled(monkeypatch: pytest.MonkeyPatch) -> list[WebhookEvent]:
    events: list[WebhookEvent] = []

    async def handle(event: WebhookEvent) -> None:
        events.append(event)

    monkeypatch.setattr(routes.service, "handle_notion_event", handle)
    return events


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _event(
    *,
    author_type: str = "person",
    event_type: str = "page.properties_updated",
    author_id: str = "user-1",
) -> bytes:
    return json.dumps(
        {
            "id": "evt-1",
            "type": event_type,
            "entity": {"id": "page-1", "type": "page"},
            "authors": [{"id": author_id, "type": author_type}],
            "data": {"updated_properties": ["%40JSP"]},
        }
    ).encode()


def _post(client: TestClient, body: bytes, signature: str | None) -> tuple[int, dict[str, str]]:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Notion-Signature"] = signature
    resp = client.post("/webhooks/notion", content=body, headers=headers)
    return resp.status_code, resp.json()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_signed_event_is_handled(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, handled: list[WebhookEvent]
) -> None:
    monkeypatch.setenv("NOTION_WEBHOOK_SECRET", SECRET)
    body = _event()

    status, payload = _post(client, body, _sign(body))

    assert (status, payload["status"]) == (200, "accepted")
    assert [event.entity.id for event in handled] == ["page-1"]


@pytest.mark.parametrize("signature", [None, "sha256=deadbeef"])
def test_unsigned_or_forged_event_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    handled: list[WebhookEvent],
    signature: str | None,
) -> None:
    monkeypatch.setenv("NOTION_WEBHOOK_SECRET", SECRET)

    status, _ = _post(client, _event(), signature)

    assert status == 401
    assert handled == []


def test_events_are_rejected_until_a_secret_is_configured(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, handled: list[WebhookEvent]
) -> None:
    monkeypatch.delenv("NOTION_WEBHOOK_SECRET", raising=False)
    body = _event()

    status, _ = _post(client, body, _sign(body))

    assert status == 401
    assert handled == []


def test_verification_token_is_shown_in_the_log_message_until_configured(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    handled: list[WebhookEvent],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("NOTION_WEBHOOK_SECRET", raising=False)
    body = json.dumps({"verification_token": SECRET}).encode()

    with caplog.at_level("WARNING", logger=routes.logger.name):
        assert _post(client, body, None) == (
            200,
            {"status": "ok", "message": "Verification token received"},
        )
    assert any(SECRET in record.getMessage() for record in caplog.records)

    caplog.clear()
    monkeypatch.setenv("NOTION_WEBHOOK_SECRET", SECRET)
    with caplog.at_level("WARNING", logger=routes.logger.name):
        status, payload = _post(client, body, None)
    assert (status, payload["status"]) == (200, "ignored")
    assert not any(SECRET in record.getMessage() for record in caplog.records)
    assert handled == []


@pytest.mark.parametrize(
    ("author_type", "event_type"),
    [("bot", "page.properties_updated"), ("person", "page.deleted")],
)
def test_integration_authored_and_unhandled_events_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    handled: list[WebhookEvent],
    author_type: str,
    event_type: str,
) -> None:
    monkeypatch.setenv("NOTION_WEBHOOK_SECRET", SECRET)
    body = _event(author_type=author_type, event_type=event_type)

    status, payload = _post(client, body, _sign(body))

    assert (status, payload["status"]) == (200, "ignored")
    assert handled == []


def test_events_authored_by_the_agent_member_are_ignored(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, handled: list[WebhookEvent]
) -> None:
    monkeypatch.setenv("NOTION_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("NOTION_AGENT_USER_IDS", "3e6d872b-594c-8176-bb78-000243d8cbe3")
    body = _event(author_id="3e6d872b594c8176bb78000243d8cbe3")

    status, payload = _post(client, body, _sign(body))

    assert (status, payload["status"]) == (200, "ignored")
    assert handled == []
