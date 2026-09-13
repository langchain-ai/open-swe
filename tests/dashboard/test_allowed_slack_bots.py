from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import profiles, routes


@pytest.fixture
def directory() -> dict[str, Any]:
    return {"pages": {}, "calls": [], "auth_calls": 0, "error": None}


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, fake_store: Any, directory: dict[str, Any], slack_api
) -> TestClient:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice,bob")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-slack-token")
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value="test-token"))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_session] = lambda: {
        "sub": "alice",
        "email": "alice@example.com",
    }

    def slack(method, params, headers):
        assert headers["authorization"] in {
            "Bearer test-slack-token",
            "Bearer another-test-token",
        }
        if method == "auth.test":
            directory["auth_calls"] += 1
            data = {"ok": True, "team_id": "T123", "user_id": "UOWN", "bot_id": "BOWN"}
        elif method == "users.list":
            directory["calls"].append(str(params.get("cursor", "")))
            if directory["error"]:
                return (
                    directory["error"].status_code,
                    directory["error"].json(),
                    dict(directory["error"].headers),
                )
            data = directory["pages"].get(params.get("cursor", ""), {"ok": True, "members": []})
        elif method == "users.info":
            user_id = params["user"]
            if user_id == "UMISSING":
                return 200, {"ok": True, "user": None}, {}
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
            assert method == "bots.info"
            bot_id = params["bot"]
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
        return 200, data, {}

    slack_api.handler = slack
    return TestClient(app)


def test_admin_can_add_list_and_remove_bot(client: TestClient, directory: dict[str, Any]) -> None:
    assert client.get("/dashboard/api/slack/bots").json() == []
    assert client.get("/dashboard/api/slack/allowed-bots").json() == []
    response = client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": " U123 "})
    assert response.status_code == 200, response.text
    bot = response.json()
    assert {key: bot[key] for key in ("team_id", "bot_id", "user_id", "created_by", "name")} == {
        "team_id": "T123",
        "bot_id": "B123",
        "user_id": "U123",
        "created_by": "alice",
        "name": "Release bot",
    }
    assert client.get("/dashboard/api/slack/allowed-bots").json() == [bot]
    assert directory["auth_calls"] == 1
    assert client.delete("/dashboard/api/slack/allowed-bots/T123/B123").status_code == 200
    assert client.get("/dashboard/api/slack/allowed-bots").json() == []


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/dashboard/api/slack/allowed-bots", None),
        ("GET", "/dashboard/api/slack/bots", None),
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
    assert client.get("/dashboard/api/slack/allowed-bots").json()[0]["created_by"] == "alice"


def test_system_bot_does_not_require_admin_oauth(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value=None))
    assert (
        client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": "B123"}).status_code == 200
    )


def _member(user_id: str, name: str, **overrides: Any) -> dict[str, Any]:
    return {
        "id": user_id,
        "team_id": "T123",
        "name": name,
        "deleted": False,
        "is_bot": True,
        "profile": {
            "bot_id": "B" + user_id[1:],
            "display_name": name,
            "image_48": "https://avatars.slack-edge.com/bot.png",
            "email": "private@example.com",
        },
        **overrides,
    }


def test_bot_directory_paginates_filters_and_caches(
    client: TestClient, directory: dict[str, Any]
) -> None:
    directory["pages"] = {
        "": {
            "ok": True,
            "members": [
                _member("UHUMAN", "Human", is_bot=False),
                _member("UDELETED", "Deleted", deleted=True),
                _member("UOWN", "Open SWE"),
                _member("USLACKBOT", "slackbot"),
                _member("UOTHER", "Other workspace", team_id="TOTHER"),
            ],
            "response_metadata": {"next_cursor": "next-page"},
        },
        "next-page": {
            "ok": True,
            "members": [_member("U123", "Release bot"), _member("U456", "Build bot")],
        },
    }
    response = client.get("/dashboard/api/slack/bots")
    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "team_id": "T123",
            "bot_id": "B456",
            "user_id": "U456",
            "name": "Build bot",
            "image_url": "https://avatars.slack-edge.com/bot.png",
        },
        {
            "team_id": "T123",
            "bot_id": "B123",
            "user_id": "U123",
            "name": "Release bot",
            "image_url": "https://avatars.slack-edge.com/bot.png",
        },
    ]
    assert client.get("/dashboard/api/slack/bots").json() == response.json()
    assert directory["calls"] == ["", "next-page"]


def test_bot_directory_does_not_reuse_another_installation_cache(
    client: TestClient, directory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory["pages"] = {"": {"ok": True, "members": [_member("U123", "Release bot")]}}
    response = client.get("/dashboard/api/slack/bots")
    assert response.status_code == 200
    assert len(response.json()) == 1
    monkeypatch.setenv("SLACK_BOT_TOKEN", "another-test-token")
    directory["pages"] = {"": {"ok": True, "members": []}}
    assert client.get("/dashboard/api/slack/bots").json() == []
    assert directory["auth_calls"] == 2


def test_bot_directory_reports_rate_limit_without_partial_results(
    client: TestClient, directory: dict[str, Any]
) -> None:
    directory["error"] = httpx2.Response(
        429, headers={"Retry-After": "30"}, json={"ok": False, "error": "ratelimited"}
    )
    response = client.get("/dashboard/api/slack/bots")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert len(directory["calls"]) == 1


def test_bot_directory_reports_missing_scope(client: TestClient, directory: dict[str, Any]) -> None:
    directory["error"] = httpx2.Response(
        200, json={"ok": False, "error": "missing_scope", "needed": "users:read"}
    )
    response = client.get("/dashboard/api/slack/bots")
    assert response.status_code == 400
    assert "users:read" in response.json()["detail"]


def test_bot_directory_rejects_repeated_pagination_cursor(
    client: TestClient, directory: dict[str, Any]
) -> None:
    page = {"ok": True, "members": [], "response_metadata": {"next_cursor": "same-cursor"}}
    directory["pages"] = {"": page, "same-cursor": page}
    assert client.get("/dashboard/api/slack/bots").status_code == 502


def test_selected_bot_is_reverified_at_add_time(
    client: TestClient, directory: dict[str, Any]
) -> None:
    directory["pages"] = {"": {"ok": True, "members": [_member("UHUMAN", "Former bot")]}}
    response = client.get("/dashboard/api/slack/bots")
    assert response.status_code == 200
    assert len(response.json()) == 1
    response = client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": "UHUMAN"})
    assert response.status_code == 400
    assert client.get("/dashboard/api/slack/allowed-bots").json() == []
