from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from agent.dashboard import langsmith_oauth
from agent.dashboard.oauth_credentials import load_credential, save_credential
from agent.encryption import decrypt_token, encrypt_token
from agent.users.models import User

TOKEN_ENDPOINT = "https://api.smith.langchain.com/oauth/token"


@pytest.fixture(autouse=True)
async def connected(registry_db, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("LANGSMITH_OAUTH_CLIENT_ID", "lsc_open_swe")
    monkeypatch.setenv("LANGSMITH_OAUTH_CLIENT_SECRET", "open-swe-secret")
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    await User.sign_in("github", "1", login="alice")
    await save_credential(
        "langsmith",
        "alice",
        encrypted_access_token=encrypt_token("old-access"),
        encrypted_refresh_token=encrypt_token("old-refresh"),
        access_token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        client_id="lsc_open_swe",
        token_endpoint=TOKEN_ENDPOINT,
        account_email="alice@example.com",
    )


async def test_expired_token_is_refreshed_and_kept_for_the_next_call(monkeypatch):
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
    assert calls[0][1]["client_secret"] == "open-swe-secret"
    stored = await load_credential("langsmith", "alice")
    assert stored is not None
    # A refresh response without a new refresh token keeps the one already stored.
    assert decrypt_token(stored.encrypted_refresh_token or "") == "old-refresh"
    assert stored.account_email == "alice@example.com"


async def test_revoked_grant_disconnects_instead_of_failing_every_call(monkeypatch):
    async def post(url, *, fallback, json=None, data=None):
        raise langsmith_oauth.LangSmithOAuthError(400, "revoked", error_code="invalid_grant")

    monkeypatch.setattr(langsmith_oauth, "_post", post)
    assert await langsmith_oauth.langsmith_access_token("alice") is None
    assert (await langsmith_oauth.langsmith_status("alice"))["connected"] is False


async def test_reconnecting_as_another_account_replaces_the_old_identity(monkeypatch):
    async def email(access_token):
        return "bob@example.com"

    monkeypatch.setattr(langsmith_oauth, "_email", email)
    await langsmith_oauth._save_tokens(
        "alice",
        {"access_token": "bob-access", "expires_in": 300},
        client_id="lsc_open_swe",
        token_endpoint=TOKEN_ENDPOINT,
        new_grant=True,
    )
    stored = await load_credential("langsmith", "alice")
    assert stored is not None
    assert stored.account_email == "bob@example.com"
    # The previous account's refresh token must not be paired with the new grant.
    assert stored.encrypted_refresh_token is None
