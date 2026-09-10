from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import profiles, routes


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, fake_store: Any) -> TestClient:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice,bob")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-slack-token")
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value="test-token"))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_session] = lambda: {
        "sub": "alice",
        "email": "alice@example.com",
    }

    def slack(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/auth.test":
            data = {"ok": True, "team_id": "T123", "user_id": "UOWN", "bot_id": "BOWN"}
        elif request.url.path == "/api/users.info":
            user_id = request.url.params["user"]
            if user_id == "UMISSING":
                return httpx2.Response(200, json={"ok": True, "user": None})
            data = {
                "ok": True,
                "user": {
                    "id": user_id,
                    "team_id": "T123",
                    "is_bot": user_id != "UHUMAN",
                    "profile": {"bot_id": "B123"},
                },
            }
        else:
            assert request.url.path == "/api/bots.info"
            bot_id = request.url.params["bot"]
            data = {
                "ok": True,
                "bot": {
                    "id": bot_id,
                    "user_id": {"BOWN": "UOWN", "BMISSING": "UMISSING"}.get(bot_id, "U123"),
                    "app_id": "A123",
                    "name": "Release bot",
                    "deleted": False,
                },
            }
        return httpx2.Response(200, json=data)

    async_client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: async_client(**kwargs, transport=httpx2.MockTransport(slack)),
    )
    return TestClient(app)


def test_admin_can_add_list_and_remove_bot(client: TestClient) -> None:
    assert client.get("/dashboard/api/slack/allowed-bots").json() == []
    response = client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": " U123 "})
    assert response.status_code == 200, response.text
    bot = response.json()
    assert {key: bot[key] for key in ("team_id", "bot_id", "user_id", "github_login", "name")} == {
        "team_id": "T123",
        "bot_id": "B123",
        "user_id": "U123",
        "github_login": "alice",
        "name": "Release bot",
    }
    assert client.get("/dashboard/api/slack/allowed-bots").json() == [bot]
    assert client.delete("/dashboard/api/slack/allowed-bots/T123/B123").status_code == 200
    assert client.get("/dashboard/api/slack/allowed-bots").json() == []


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/dashboard/api/slack/allowed-bots", None),
        ("POST", "/dashboard/api/slack/allowed-bots", {"bot_id": "B123"}),
        ("DELETE", "/dashboard/api/slack/allowed-bots/T123/B123", None),
    ],
)
def test_non_admin_cannot_manage_bots(
    client: TestClient, method: str, path: str, body: Any
) -> None:
    client.app.dependency_overrides[routes.require_session] = lambda: {"sub": "mallory"}
    assert client.request(method, path, json=body).status_code == 403


@pytest.mark.parametrize("bot_id", ["*", "Release bot", "B123,B456", "UHUMAN", "BOWN", "BMISSING"])
def test_rejects_invalid_human_and_self_bots(client: TestClient, bot_id: str) -> None:
    response = client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": bot_id})
    assert response.status_code in (400, 422), response.text
    assert client.get("/dashboard/api/slack/allowed-bots").json() == []


def test_cannot_supply_another_execution_identity(client: TestClient) -> None:
    response = client.post(
        "/dashboard/api/slack/allowed-bots",
        json={
            "bot_id": "B123",
            "github_login": "victim",
        },
    )
    assert response.status_code == 422


def test_duplicate_add_does_not_transfer_ownership(client: TestClient) -> None:
    assert (
        client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": "B123"}).status_code == 200
    )
    client.app.dependency_overrides[routes.require_session] = lambda: {"sub": "bob"}
    response = client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": "B123"})
    assert response.status_code == 409
    assert client.get("/dashboard/api/slack/allowed-bots").json()[0]["github_login"] == "alice"


def test_owner_must_have_github_authorization(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value=None))
    assert (
        client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": "B123"}).status_code == 400
    )
