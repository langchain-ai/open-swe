from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from agent.dashboard import notion_oauth as no


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
    monkeypatch.setattr(no, "_client", lambda: _FakeClient(store))
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
