from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from openswe.dashboard import user_credentials as uc
from openswe.dashboard.notion_oauth import NotionOAuthError
from tests.conftest import FakeUserRecords


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.mark.asyncio
async def test_notion_roundtrip_and_redaction(user_records: FakeUserRecords) -> None:
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

    record = user_records.items[("credential", "alice", "notion")]
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
async def test_notion_refresh_rotates_tokens(user_records: FakeUserRecords) -> None:
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
    record = user_records.items[("credential", "alice", "notion")]
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
async def test_notion_invalid_grant_disconnects(user_records: FakeUserRecords) -> None:
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
    record = user_records.items[("credential", "alice", "notion")]
    record["token_expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    with patch.object(
        uc,
        "refresh_notion_access_token",
        new_callable=AsyncMock,
        side_effect=NotionOAuthError(400, "dead", error_code="invalid_grant"),
    ):
        assert await uc.get_notion_credentials("alice") is None

    assert await uc.get_notion_status("alice") == {"notion": {"connected": False}}
