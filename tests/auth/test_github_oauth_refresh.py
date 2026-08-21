from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from support.langgraph_fakes import FakeLangGraphClient, FakeStore

from agent.dashboard import github_tokens
from agent.dashboard.github_tokens import (
    OAUTH_TOKENS_NAMESPACE,
    GithubOAuthError,
    get_valid_access_token,
    is_unrecoverable_refresh_error,
)
from agent.encryption import decrypt_token, encrypt_token

_ITEM = (tuple(OAUTH_TOKENS_NAMESPACE), "octo")


@pytest.fixture()
def fake_store(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
    monkeypatch: pytest.MonkeyPatch,
) -> FakeStore:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return patched_langgraph_client().store


def _seed(
    fake_store: FakeStore,
    *,
    access_token: str,
    refresh_token: str,
    expires_at: str,
) -> dict[str, Any]:
    record = {
        "login": "octo",
        "email": "u@example.com",
        "encrypted_gh_token": encrypt_token(access_token),
        "encrypted_gh_refresh_token": encrypt_token(refresh_token),
        "token_expires_at": expires_at,
    }
    fake_store.items[_ITEM] = record
    return record


def _soon() -> str:
    return (datetime.now(UTC) + timedelta(minutes=1)).isoformat()


def test_is_unrecoverable_refresh_error() -> None:
    assert is_unrecoverable_refresh_error(
        GithubOAuthError(400, "x", error_code="bad_refresh_token")
    )
    assert is_unrecoverable_refresh_error(
        GithubOAuthError(400, "x", error_code="unauthorized_client")
    )
    assert not is_unrecoverable_refresh_error(GithubOAuthError(400, "x", error_code="slow_down"))
    assert not is_unrecoverable_refresh_error(GithubOAuthError(400, "x"))
    assert not is_unrecoverable_refresh_error(RuntimeError("boom"))


@pytest.mark.asyncio
async def test_get_valid_access_token_refreshes_when_near_expiry(fake_store: FakeStore) -> None:
    _seed(fake_store, access_token="old-access", refresh_token="ghr_test", expires_at=_soon())

    with patch.object(
        github_tokens,
        "refresh_user_access_token",
        new_callable=AsyncMock,
        return_value={
            "access_token": "new-access",
            "refresh_token": "ghr_new",
            "expires_in": 28800,
            "refresh_token_expires_in": 15897600,
        },
    ) as refresh:
        token = await get_valid_access_token("octo")

    assert token == "new-access"
    refresh.assert_awaited_once_with("ghr_test")
    stored = fake_store.items[_ITEM]
    assert decrypt_token(stored["encrypted_gh_token"]) == "new-access"
    assert decrypt_token(stored["encrypted_gh_refresh_token"]) == "ghr_new"
    assert stored["email"] == "u@example.com"
    assert datetime.fromisoformat(stored["token_expires_at"]) > datetime.now(UTC)


@pytest.mark.asyncio
async def test_get_valid_access_token_drops_record_on_dead_refresh_token(
    fake_store: FakeStore,
) -> None:
    _seed(fake_store, access_token="stale-access", refresh_token="ghr_dead", expires_at=_soon())

    with patch.object(
        github_tokens,
        "refresh_user_access_token",
        new_callable=AsyncMock,
        side_effect=GithubOAuthError(
            400, "github oauth error: bad refresh token", error_code="bad_refresh_token"
        ),
    ):
        token = await get_valid_access_token("octo")

    assert token is None
    assert _ITEM not in fake_store.items
    assert fake_store.deleted == [_ITEM]


@pytest.mark.asyncio
async def test_get_valid_access_token_keeps_fresh_reauth_on_dead_refresh_token(
    fake_store: FakeStore,
) -> None:
    _seed(fake_store, access_token="stale-access", refresh_token="ghr_dead", expires_at=_soon())

    async def reauthorize_then_fail(_refresh_token: str) -> dict[str, Any]:
        # A re-login lands while the refresh request is in flight.
        fake_store.items[_ITEM] = {
            "login": "octo",
            "email": "u@example.com",
            "encrypted_gh_token": encrypt_token("fresh-access"),
            "encrypted_gh_refresh_token": encrypt_token("ghr_fresh"),
            "token_expires_at": (datetime.now(UTC) + timedelta(hours=8)).isoformat(),
        }
        raise GithubOAuthError(
            400, "github oauth error: bad refresh token", error_code="bad_refresh_token"
        )

    with patch.object(github_tokens, "refresh_user_access_token", reauthorize_then_fail):
        token = await get_valid_access_token("octo")

    assert token == "fresh-access"
    assert fake_store.deleted == []


@pytest.mark.asyncio
async def test_get_valid_access_token_keeps_record_on_transient_refresh_failure(
    fake_store: FakeStore,
) -> None:
    _seed(fake_store, access_token="still-usable", refresh_token="ghr_ok", expires_at=_soon())

    with patch.object(
        github_tokens,
        "refresh_user_access_token",
        new_callable=AsyncMock,
        side_effect=GithubOAuthError(503, "github oauth temporarily unavailable"),
    ):
        token = await get_valid_access_token("octo")

    assert token == "still-usable"
    assert fake_store.deleted == []
    assert decrypt_token(fake_store.items[_ITEM]["encrypted_gh_token"]) == "still-usable"


@pytest.mark.asyncio
async def test_get_valid_access_token_returns_stored_when_not_expiring(
    fake_store: FakeStore,
) -> None:
    _seed(
        fake_store,
        access_token="still-good",
        refresh_token="ghr_ok",
        expires_at=(datetime.now(UTC) + timedelta(hours=5)).isoformat(),
    )

    with patch.object(
        github_tokens, "refresh_user_access_token", new_callable=AsyncMock
    ) as refresh:
        token = await get_valid_access_token("octo")

    assert token == "still-good"
    refresh.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_from_github_response_keeps_the_prior_refresh_token(
    fake_store: FakeStore,
) -> None:
    _seed(fake_store, access_token="old-access", refresh_token="ghr_kept", expires_at=_soon())

    await github_tokens.upsert_access_token_from_github_response(
        "octo", "", {"access_token": "rotated-access", "expires_in": 28800}
    )

    stored = fake_store.items[_ITEM]
    assert decrypt_token(stored["encrypted_gh_token"]) == "rotated-access"
    assert decrypt_token(stored["encrypted_gh_refresh_token"]) == "ghr_kept"
    assert stored["email"] == "u@example.com"


@pytest.mark.asyncio
async def test_upsert_from_github_response_ignores_a_response_without_a_token(
    fake_store: FakeStore,
) -> None:
    await github_tokens.upsert_access_token_from_github_response(
        "octo", "u@example.com", {"error": "bad_verification_code"}
    )

    assert fake_store.items == {}


@pytest.mark.asyncio
async def test_has_access_token_record(fake_store: FakeStore) -> None:
    assert await github_tokens.has_access_token_record("octo") is False
    _seed(fake_store, access_token="access", refresh_token="ghr", expires_at=_soon())
    assert await github_tokens.has_access_token_record("octo") is True


@pytest.mark.asyncio
async def test_get_oauth_token_record_carries_expiry_metadata(fake_store: FakeStore) -> None:
    seeded = _seed(fake_store, access_token="access", refresh_token="ghr", expires_at=_soon())

    assert await github_tokens.get_oauth_token_record("octo") == seeded
