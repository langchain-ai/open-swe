from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from agent.dashboard import langsmith_oauth
from agent.encryption import decrypt_token, encrypt_token

TOKEN_ENDPOINT = "https://api.smith.langchain.com/oauth/token"


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


def seed_expired(fake_store, login="alice"):
    fake_store.seed(
        ["user_credentials", login],
        "langsmith",
        {
            "encrypted_access_token": encrypt_token("old-access"),
            "encrypted_refresh_token": encrypt_token("old-refresh"),
            "token_expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "client_id": "lsc_open_swe",
            "token_endpoint": TOKEN_ENDPOINT,
            "email": "alice@example.com",
        },
    )


async def test_expired_token_is_refreshed_and_kept_for_the_next_call(fake_store, monkeypatch):
    seed_expired(fake_store)
    calls = []

    async def post(url, *, fallback, json=None, data=None):
        calls.append((url, data))
        return {"access_token": "new-access", "expires_in": 3600}

    monkeypatch.setattr(langsmith_oauth, "_post", post)
    assert await langsmith_oauth.langsmith_access_token("Alice") == "new-access"
    assert await langsmith_oauth.langsmith_access_token("alice") == "new-access"
    assert len(calls) == 1
    assert calls[0][1]["grant_type"] == "refresh_token"
    assert calls[0][1]["resource"] == "https://api.smith.langchain.com"
    stored = fake_store.values(["user_credentials", "alice"])["langsmith"]
    # A refresh response without a new refresh token keeps the one already stored.
    assert decrypt_token(stored["encrypted_refresh_token"]) == "old-refresh"


async def test_revoked_grant_disconnects_instead_of_failing_every_call(fake_store, monkeypatch):
    seed_expired(fake_store)

    async def post(url, *, fallback, json=None, data=None):
        raise langsmith_oauth.LangSmithOAuthError(400, "revoked", error_code="invalid_grant")

    monkeypatch.setattr(langsmith_oauth, "_post", post)
    assert await langsmith_oauth.langsmith_access_token("alice") is None
    assert (await langsmith_oauth.langsmith_status("alice"))["connected"] is False
