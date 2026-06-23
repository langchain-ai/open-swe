from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from agent.dashboard import user_credentials as uc
from agent.dashboard.notion_oauth import NotionOAuthError
from agent.dashboard.user_credentials import CurrentsCredentialsUpdate


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
    monkeypatch.setattr(uc, "_client", lambda: _FakeClient(store))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return store


class TestValidators:
    def test_empty_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CurrentsCredentialsUpdate(api_key="")

    def test_whitespace_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CurrentsCredentialsUpdate(api_key="  ")

    def test_key_trimmed(self) -> None:
        u = CurrentsCredentialsUpdate(api_key="  secret  ")
        assert u.api_key == "secret"


@pytest.mark.asyncio
async def test_currents_roundtrip_and_redaction(fake_store: _FakeStore) -> None:
    status = await uc.connect_currents(
        "alice", CurrentsCredentialsUpdate(api_key="secret-currents-key-1234")
    )
    assert status["currents"]["connected"] is True
    assert status["currents"]["api_key_last4"] == "1234"

    record = fake_store.items[(("user_credentials", "alice"), "currents")]
    assert record["encrypted_api_key"] != "secret-currents-key-1234"

    api_key = await uc.get_currents_api_key("alice")
    assert api_key == "secret-currents-key-1234"

    after = await uc.disconnect_currents("alice")
    assert after["currents"]["connected"] is False
    assert await uc.get_currents_api_key("alice") is None


@pytest.mark.asyncio
async def test_currents_isolation_between_users(fake_store: _FakeStore) -> None:
    await uc.connect_currents("alice", CurrentsCredentialsUpdate(api_key="alice-key-abcd"))
    await uc.connect_currents("bob", CurrentsCredentialsUpdate(api_key="bob-key-wxyz"))

    assert await uc.get_currents_api_key("alice") == "alice-key-abcd"
    assert await uc.get_currents_api_key("bob") == "bob-key-wxyz"

    await uc.disconnect_currents("alice")
    assert await uc.get_currents_api_key("alice") is None
    assert await uc.get_currents_api_key("bob") == "bob-key-wxyz"


@pytest.mark.asyncio
async def test_currents_status_when_not_connected(fake_store: _FakeStore) -> None:
    status = await uc.get_currents_status("nobody")
    assert status["currents"]["connected"] is False


@pytest.mark.asyncio
async def test_get_currents_api_key_none_when_not_connected(fake_store: _FakeStore) -> None:
    assert await uc.get_currents_api_key("nobody") is None


@pytest.mark.asyncio
async def test_notion_roundtrip_and_redaction(fake_store: _FakeStore) -> None:
    status = await uc.connect_notion(
        "alice",
        {
            "access_token": "notion-access-1234",
            "refresh_token": "notion-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_endpoint": "https://mcp.notion.com/token",
        },
    )
    assert status["notion"]["connected"] is True

    record = fake_store.items[(("user_credentials", "alice"), "notion")]
    assert record["encrypted_access_token"] != "notion-access-1234"
    assert record["encrypted_refresh_token"] != "notion-refresh"
    assert record["encrypted_client_secret"] != "client-secret"

    creds = await uc.get_notion_credentials("alice")
    assert creds is not None
    assert creds.access_token == "notion-access-1234"
    assert creds.refresh_token == "notion-refresh"
    assert creds.client_id == "client-id"
    assert creds.client_secret == "client-secret"

    after = await uc.disconnect_notion("alice")
    assert after["notion"]["connected"] is False
    assert await uc.get_notion_credentials("alice") is None


@pytest.mark.asyncio
async def test_notion_refresh_rotates_tokens(fake_store: _FakeStore) -> None:
    await uc.connect_notion(
        "alice",
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_in": 3600,
        },
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_endpoint": "https://mcp.notion.com/token",
        },
    )
    record = fake_store.items[(("user_credentials", "alice"), "notion")]
    record["token_expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    with patch.object(
        uc,
        "refresh_notion_access_token",
        new_callable=AsyncMock,
        return_value={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        },
    ) as refresh:
        creds = await uc.get_notion_credentials("alice")

    assert creds is not None
    assert creds.access_token == "new-access"
    assert creds.refresh_token == "new-refresh"
    refresh.assert_awaited_once_with(
        refresh_token="old-refresh",
        token_endpoint="https://mcp.notion.com/token",
        client_id="client-id",
        client_secret="client-secret",
    )


@pytest.mark.asyncio
async def test_notion_invalid_grant_disconnects(fake_store: _FakeStore) -> None:
    await uc.connect_notion(
        "alice",
        {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_in": 3600,
        },
        {
            "client_id": "client-id",
            "token_endpoint": "https://mcp.notion.com/token",
        },
    )
    record = fake_store.items[(("user_credentials", "alice"), "notion")]
    record["token_expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    with patch.object(
        uc,
        "refresh_notion_access_token",
        new_callable=AsyncMock,
        side_effect=NotionOAuthError(400, "dead", error_code="invalid_grant"),
    ):
        assert await uc.get_notion_credentials("alice") is None

    assert await uc.get_notion_status("alice") == {"notion": {"connected": False}}
