from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openswe import store as agent_store
from openswe.dashboard import notion_oauth as no
from openswe.dashboard import notion_routes
from openswe.dashboard.oauth import COOKIE_NAME, issue_session
from openswe.users import User
from openswe.users.models import UserIdentity


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str):
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def delete_item(self, namespace: list[str], key: str) -> None:
        self.items.pop((tuple(namespace), key), None)


class _FakeClient:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store


@pytest.fixture()
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    store = _FakeStore()
    monkeypatch.setattr(agent_store, "store_client", lambda: _FakeClient(store))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return store


def test_code_challenge_matches_rfc7636_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert no.code_challenge_for_verifier(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_build_notion_authorize_url() -> None:
    url = no.build_notion_authorize_url(
        authorization_endpoint="https://mcp.notion.com/authorize",
        client_id="cid",
        redirect_uri="https://example.com/dashboard/api/notion/callback",
        code_challenge="challenge",
        state="state-token",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "mcp.notion.com"
    assert parsed.path == "/authorize"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["cid"]
    assert query["code_challenge"] == ["challenge"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["prompt"] == ["consent"]


def test_build_notion_authorize_url_rejects_other_hosts() -> None:
    with pytest.raises(no.NotionOAuthError):
        no.build_notion_authorize_url(
            authorization_endpoint="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://example.com/callback",
            code_challenge="challenge",
            state="state-token",
        )


@pytest.mark.asyncio
async def test_store_and_pop_notion_oauth_flow(
    fake_store: _FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        no,
        "discover_notion_oauth_metadata",
        AsyncMock(
            return_value={
                "authorization_endpoint": "https://mcp.notion.com/authorize",
                "token_endpoint": "https://mcp.notion.com/token",
                "registration_endpoint": "https://mcp.notion.com/register",
            }
        ),
    )
    monkeypatch.setattr(
        no,
        "register_notion_oauth_client",
        AsyncMock(return_value={"client_id": "cid", "client_secret": "secret"}),
    )
    monkeypatch.setattr(no, "generate_code_verifier", lambda: "verifier")

    url = await no.store_notion_oauth_flow(
        "alice",
        "nonce-hash",
        redirect_uri="https://example.com/dashboard/api/notion/callback",
        state="state-token",
    )
    assert parse_qs(urlparse(url).query)["client_id"] == ["cid"]

    flow = await no.pop_notion_oauth_flow("alice", "nonce-hash")
    assert flow is not None
    assert flow["code_verifier"] == "verifier"
    assert flow["client_secret"] == "secret"
    assert await no.pop_notion_oauth_flow("alice", "nonce-hash") is None


@pytest.mark.parametrize(
    ("target", "expected", "delivery"),
    [
        (
            "/agents/thread-1?from=chat#latest",
            "https://dashboard.example/agents/thread-1?from=chat#latest",
            True,
        ),
        ("https://evil.example/steal", "https://dashboard.example", True),
        (None, "https://dashboard.example/my-settings/connections", True),
        ("slack", "Notion connected. A confirmation was sent", True),
        ("slack", "Notion connected, but we couldn't send a Slack confirmation.", False),
        (
            "slack",
            "Notion connected, but we couldn't send a Slack confirmation.",
            RuntimeError("Slack unavailable"),
        ),
    ],
)
def test_notion_browser_connection_returns_to_safe_target(
    fake_store: _FakeStore,
    monkeypatch: pytest.MonkeyPatch,
    target: str | None,
    expected: str,
    delivery: bool | Exception,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")
    monkeypatch.setattr(
        no,
        "discover_notion_oauth_metadata",
        AsyncMock(
            return_value={
                "authorization_endpoint": "https://mcp.notion.com/authorize",
                "token_endpoint": "https://mcp.notion.com/token",
                "registration_endpoint": "https://mcp.notion.com/register",
            }
        ),
    )
    monkeypatch.setattr(
        no, "register_notion_oauth_client", AsyncMock(return_value={"client_id": "cid"})
    )
    exchange = AsyncMock(return_value={"access_token": "notion-token"})
    monkeypatch.setattr(notion_routes, "exchange_notion_code", exchange)
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    user = User(id=user_id, identities=[UserIdentity(provider="slack", external_id="U_ALICE")])
    get_user = AsyncMock(return_value=user)
    monkeypatch.setattr(User, "get", get_user)
    notify = AsyncMock(
        return_value=delivery if isinstance(delivery, bool) else False,
        side_effect=delivery if isinstance(delivery, Exception) else None,
    )
    monkeypatch.setattr(notion_routes, "send_dm", notify)
    app = FastAPI()
    app.include_router(notion_routes.router, prefix="/dashboard/api")
    with TestClient(app, base_url="https://dashboard.example") as client:

        def sign_in(login: str) -> None:
            client.cookies.set(
                COOKIE_NAME,
                issue_session(login=login, email=None, avatar_url=None, user_id=str(user_id)),
            )

        sign_in("alice")
        params = {"redirect_to": target} if target else {}
        if target == "slack":
            params = {
                "source": "slack",
                "redirect_to": "/agents/thread-1",
                "slack_user_id": "U_ATTACKER",
            }
        start = client.get("/dashboard/api/notion/login", params=params, follow_redirects=False)
        assert start.status_code == 302
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        nonce = client.cookies[no.NOTION_STATE_COOKIE_NAME]
        client.cookies.delete(no.NOTION_STATE_COOKIE_NAME)
        callback = {"code": "notion-code", "state": state}
        assert client.get("/dashboard/api/notion/callback", params=callback).status_code == 400
        client.cookies.set(
            no.NOTION_STATE_COOKIE_NAME,
            nonce,
            domain="dashboard.example",
            path="/dashboard/api/notion",
        )
        sign_in("bob")
        assert client.get("/dashboard/api/notion/callback", params=callback).status_code == 400
        exchange.assert_not_awaited()
        notify.assert_not_awaited()
        sign_in("alice")
        denied = client.get(
            "/dashboard/api/notion/callback",
            params={"state": state, "error": "access_denied"},
        )
        assert denied.status_code == 400
        notify.assert_not_awaited()
        response = client.get(
            "/dashboard/api/notion/callback", params=callback, follow_redirects=False
        )
        if target == "slack":
            assert response.status_code == 200
            assert "location" not in response.headers
            assert response.text.startswith(expected)
            get_user.assert_awaited_once_with(user_id)
            assert notify.await_args is not None
            assert notify.await_args.args[0] == "U_ALICE"
        else:
            assert response.status_code == 302
            assert response.headers["location"] == expected
            notify.assert_not_awaited()
        assert no.NOTION_STATE_COOKIE_NAME not in client.cookies
        assert client.get("/dashboard/api/my-credentials/notion").json()["connected"] is True
        assert client.get("/dashboard/api/notion/callback", params=callback).status_code == 400
        assert notify.await_count == (1 if target == "slack" else 0)
        sign_in("bob")
        assert client.get("/dashboard/api/my-credentials/notion").json() == {"connected": False}
