from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from fastapi import FastAPI

from openswe.dashboard import oauth, profiles, routes
from openswe.users.models import User


@pytest.fixture
def directory() -> dict[str, Any]:
    return {"pages": {}, "calls": [], "auth_calls": 0, "error": None}


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[oauth.require_session] = lambda: {
        "sub": "alice",
        "email": "alice@example.com",
    }
    return app


@pytest.fixture
async def client(
    registry_db: None,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    directory: dict[str, Any],
    slack_api,
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice,bob")
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob")
    await User.sign_in("github", "1", login="alice")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-slack-token")
    monkeypatch.setattr(profiles, "get_valid_access_token", AsyncMock(return_value="test-token"))

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
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_admin_can_add_list_and_remove_bot(
    client: httpx.AsyncClient, directory: dict[str, Any]
) -> None:
    assert (await client.get("/dashboard/api/slack/bots")).json() == []
    assert (await client.get("/dashboard/api/slack/allowed-bots")).json() == []
    response = await client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": " U123 "})
    assert response.status_code == 200, response.text
    bot = response.json()
    assert {key: bot[key] for key in ("team_id", "bot_id", "user_id", "created_by", "name")} == {
        "team_id": "T123",
        "bot_id": "B123",
        "user_id": "U123",
        "created_by": "alice",
        "name": "Release bot",
    }
    assert (await client.get("/dashboard/api/slack/allowed-bots")).json() == [bot]
    assert directory["auth_calls"] == 1
    assert (await client.delete("/dashboard/api/slack/allowed-bots/T123/B123")).status_code == 200
    assert (await client.get("/dashboard/api/slack/allowed-bots")).json() == []


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/dashboard/api/slack/allowed-bots", None),
        ("GET", "/dashboard/api/slack/bots", None),
        ("POST", "/dashboard/api/slack/allowed-bots", {"bot_id": "B123"}),
        ("DELETE", "/dashboard/api/slack/allowed-bots/T123/B123", None),
    ],
)
async def test_non_admin_cannot_manage_bots(
    client: httpx.AsyncClient, app: FastAPI, method: str, path: str, body: Any
) -> None:
    app.dependency_overrides[oauth.require_session] = lambda: {"sub": "mallory"}
    assert (await client.request(method, path, json=body)).status_code == 403


async def test_any_user_can_read_the_bot_directory(client: httpx.AsyncClient, app: FastAPI) -> None:
    assert (
        await client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": "B123"})
    ).is_success
    app.dependency_overrides[oauth.require_session] = lambda: {"sub": "mallory"}
    response = await client.get("/dashboard/api/slack/allowed-bots/directory")
    assert response.status_code == 200, response.text
    assert response.json() == [{"key": "T123:B123", "name": "Release bot", "image_url": ""}]


async def test_cannot_supply_another_execution_identity(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/dashboard/api/slack/allowed-bots",
        json={
            "bot_id": "B123",
            "github_login": "victim",
        },
    )
    assert response.status_code == 422


async def test_duplicate_add_does_not_transfer_ownership(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    assert (
        await client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": "B123"})
    ).status_code == 200
    app.dependency_overrides[oauth.require_session] = lambda: {"sub": "bob"}
    response = await client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": "B123"})
    assert response.status_code == 409
    assert (await client.get("/dashboard/api/slack/allowed-bots")).json()[0][
        "created_by"
    ] == "alice"


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


async def test_bot_directory_does_not_reuse_another_installation_cache(
    client: httpx.AsyncClient, directory: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory["pages"] = {"": {"ok": True, "members": [_member("U123", "Release bot")]}}
    response = await client.get("/dashboard/api/slack/bots")
    assert response.status_code == 200
    assert len(response.json()) == 1
    monkeypatch.setenv("SLACK_BOT_TOKEN", "another-test-token")
    directory["pages"] = {"": {"ok": True, "members": []}}
    assert (await client.get("/dashboard/api/slack/bots")).json() == []
    assert directory["auth_calls"] == 2


async def test_bot_directory_reports_rate_limit_without_partial_results(
    client: httpx.AsyncClient, directory: dict[str, Any]
) -> None:
    directory["error"] = httpx2.Response(
        429, headers={"Retry-After": "30"}, json={"ok": False, "error": "ratelimited"}
    )
    response = await client.get("/dashboard/api/slack/bots")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert len(directory["calls"]) == 1


async def test_selected_bot_is_reverified_at_add_time(
    client: httpx.AsyncClient, directory: dict[str, Any]
) -> None:
    directory["pages"] = {"": {"ok": True, "members": [_member("UHUMAN", "Former bot")]}}
    response = await client.get("/dashboard/api/slack/bots")
    assert response.status_code == 200
    assert len(response.json()) == 1
    response = await client.post("/dashboard/api/slack/allowed-bots", json={"bot_id": "UHUMAN"})
    assert response.status_code == 400
    assert (await client.get("/dashboard/api/slack/allowed-bots")).json() == []
