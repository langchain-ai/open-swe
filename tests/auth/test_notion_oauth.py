from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import store as agent_store
from agent.dashboard import notion_oauth as no
from agent.dashboard import notion_routes
from agent.dashboard.oauth import COOKIE_NAME, issue_session


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
    ("target", "expected"),
    [
        (
            "/agents/thread-1?from=chat#latest",
            "https://dashboard.example/agents/thread-1?from=chat#latest",
        ),
        ("https://evil.example/steal", "https://dashboard.example"),
        (None, "https://dashboard.example/my-settings/connections"),
    ],
)
def test_notion_browser_connection_returns_to_safe_target(
    fake_store: _FakeStore,
    monkeypatch: pytest.MonkeyPatch,
    target: str | None,
    expected: str,
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
    app = FastAPI()
    app.include_router(notion_routes.router, prefix="/dashboard/api")
    with TestClient(app, base_url="https://dashboard.example") as client:

        def sign_in(login: str) -> None:
            client.cookies.set(
                COOKIE_NAME,
                issue_session(login=login, email=None, avatar_url=None, user_id="test-user-id"),
            )

        sign_in("alice")
        start = client.get(
            "/dashboard/api/notion/login",
            params={"redirect_to": target} if target else {},
            follow_redirects=False,
        )
        assert start.status_code == 302
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        nonce = client.cookies[no.NOTION_STATE_COOKIE_NAME]
        client.cookies.delete(no.NOTION_STATE_COOKIE_NAME)
        callback = {"code": "notion-code", "state": state}
        assert client.get("/dashboard/api/notion/callback", params=callback).status_code == 400
        client.cookies.set(no.NOTION_STATE_COOKIE_NAME, nonce)
        sign_in("bob")
        assert client.get("/dashboard/api/notion/callback", params=callback).status_code == 400
        exchange.assert_not_awaited()
        sign_in("alice")
        response = client.get(
            "/dashboard/api/notion/callback", params=callback, follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["location"] == expected
        assert client.get("/dashboard/api/my-credentials/notion").json()["connected"] is True
        sign_in("bob")
        assert client.get("/dashboard/api/my-credentials/notion").json() == {"connected": False}
