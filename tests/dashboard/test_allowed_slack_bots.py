from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import profiles, routes


@pytest.fixture
def directory() -> dict[str, Any]:
    return {"pages": {}, "calls": [], "error": None}


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, fake_store: Any, directory: dict[str, Any]
) -> TestClient:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice,bob")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-slack-token")
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value="test-token"))
    fake_store.seed(
        ["environments"],
        "backend",
        {"slug": "backend", "name": "Backend", "repos": ["langchain-ai/open-swe"]},
    )
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_session] = lambda: {
        "sub": "alice",
        "email": "alice@example.com",
    }

    def slack(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/auth.test":
            data = {"ok": True, "team_id": "T123", "user_id": "UOWN", "bot_id": "BOWN"}
        elif request.url.path == "/api/users.list":
            directory["calls"].append(str(request.url.params.get("cursor", "")))
            if directory["error"]:
                return directory["error"]
            data = directory["pages"].get(
                request.url.params.get("cursor", ""), {"ok": True, "members": []}
            )
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
    response = client.post(
        "/dashboard/api/slack/allowed-bots", json={"environment": "backend", "bot_id": " U123 "}
    )
    assert response.status_code == 200, response.text
    bot = response.json()
    assert {
        key: bot[key]
        for key in ("team_id", "bot_id", "user_id", "created_by", "environment", "name")
    } == {
        "team_id": "T123",
        "bot_id": "B123",
        "user_id": "U123",
        "created_by": "alice",
        "environment": "backend",
        "name": "Release bot",
    }
    assert client.get("/dashboard/api/slack/allowed-bots").json() == [bot]
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
    response = client.post(
        "/dashboard/api/slack/allowed-bots", json={"environment": "backend", "bot_id": bot_id}
    )
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
        client.post(
            "/dashboard/api/slack/allowed-bots", json={"environment": "backend", "bot_id": "B123"}
        ).status_code
        == 200
    )
    client.app.dependency_overrides[routes.require_session] = lambda: {"sub": "bob"}
    response = client.post(
        "/dashboard/api/slack/allowed-bots", json={"environment": "backend", "bot_id": "B123"}
    )
    assert response.status_code == 409
    assert client.get("/dashboard/api/slack/allowed-bots").json()[0]["created_by"] == "alice"


def test_system_bot_does_not_require_admin_oauth(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value=None))
    assert (
        client.post(
            "/dashboard/api/slack/allowed-bots", json={"environment": "backend", "bot_id": "B123"}
        ).status_code
        == 200
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


@pytest.mark.parametrize("environment", ["", "missing", "empty"])
def test_bot_requires_environment_with_repositories(client, fake_store, environment):
    fake_store.seed(["environments"], "empty", {"slug": "empty", "repos": []})
    response = client.post(
        "/dashboard/api/slack/allowed-bots",
        json={"bot_id": "B123", "environment": environment},
    )
    assert response.status_code in (400, 422)
    assert client.get("/dashboard/api/slack/allowed-bots").json() == []


async def test_legacy_bot_requires_reconfiguration(fake_store):
    from agent.slack.allowed_bots import resolve_allowed_slack_bot

    fake_store.seed(
        ["allowed_slack_bots"],
        "T123:B123",
        {
            "team_id": "T123",
            "bot_id": "B123",
            "name": "Legacy bot",
            "github_login": "alice",
            "created_at": "2026-09-09",
        },
    )
    assert await resolve_allowed_slack_bot("T123", "B123") is None


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
    directory["error"] = httpx2.Response(200, json={"ok": False, "error": "missing_scope"})
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
    response = client.post(
        "/dashboard/api/slack/allowed-bots", json={"environment": "backend", "bot_id": "UHUMAN"}
    )
    assert response.status_code == 400
    assert client.get("/dashboard/api/slack/allowed-bots").json() == []
