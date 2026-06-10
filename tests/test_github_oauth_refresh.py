from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.oauth import (
    GithubOAuthError,
    expires_at_from_github_response,
    is_unrecoverable_refresh_error,
)
from agent.dashboard.profiles import _token_expired, get_valid_access_token


def test_expires_at_from_github_response() -> None:
    data = {"expires_in": 3600}
    expires = expires_at_from_github_response(data, field="expires_in")
    assert expires is not None
    exp = datetime.fromisoformat(expires)
    assert exp > datetime.now(UTC)


def test_token_expired_with_skew() -> None:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    assert _token_expired(past, skew_seconds=300) is True
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert _token_expired(future, skew_seconds=300) is False
    assert _token_expired(None) is False


@pytest.mark.asyncio
async def test_get_valid_access_token_refreshes_when_near_expiry() -> None:
    soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    record = {
        "email": "u@example.com",
        "encrypted_gh_token": "enc-access",
        "encrypted_gh_refresh_token": "enc-refresh",
        "token_expires_at": soon,
    }
    with (
        patch(
            "agent.dashboard.profiles._get_value",
            new_callable=AsyncMock,
            return_value=record,
        ),
        patch("agent.dashboard.profiles._decrypt_access_token", return_value="old-access"),
        patch("agent.dashboard.profiles._decrypt_refresh_token", return_value="ghr_test"),
        patch(
            "agent.dashboard.profiles.refresh_user_access_token",
            new_callable=AsyncMock,
            return_value={
                "access_token": "new-access",
                "refresh_token": "ghr_new",
                "expires_in": 28800,
                "refresh_token_expires_in": 15897600,
            },
        ),
        patch(
            "agent.dashboard.profiles.upsert_access_token_from_github_response",
            new_callable=AsyncMock,
        ) as mock_upsert,
    ):
        token = await get_valid_access_token("octo")
    assert token == "new-access"
    mock_upsert.assert_awaited_once()


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
async def test_get_valid_access_token_drops_record_on_dead_refresh_token() -> None:
    soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    record = {
        "email": "u@example.com",
        "encrypted_gh_token": "enc-access",
        "encrypted_gh_refresh_token": "enc-refresh",
        "token_expires_at": soon,
    }
    with (
        patch(
            "agent.dashboard.profiles._get_value",
            new_callable=AsyncMock,
            return_value=record,
        ),
        patch("agent.dashboard.profiles._decrypt_access_token", return_value="stale-access"),
        patch("agent.dashboard.profiles._decrypt_refresh_token", return_value="ghr_dead"),
        patch(
            "agent.dashboard.profiles.refresh_user_access_token",
            new_callable=AsyncMock,
            side_effect=GithubOAuthError(
                400, "github oauth error: bad refresh token", error_code="bad_refresh_token"
            ),
        ),
        patch(
            "agent.dashboard.profiles.delete_access_token",
            new_callable=AsyncMock,
        ) as mock_delete,
    ):
        token = await get_valid_access_token("octo")
    assert token is None
    mock_delete.assert_awaited_once_with("octo")


@pytest.mark.asyncio
async def test_get_valid_access_token_keeps_record_on_transient_refresh_failure() -> None:
    soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    record = {
        "email": "u@example.com",
        "encrypted_gh_token": "enc-access",
        "encrypted_gh_refresh_token": "enc-refresh",
        "token_expires_at": soon,
    }
    with (
        patch(
            "agent.dashboard.profiles._get_value",
            new_callable=AsyncMock,
            return_value=record,
        ),
        patch("agent.dashboard.profiles._decrypt_access_token", return_value="still-usable"),
        patch("agent.dashboard.profiles._decrypt_refresh_token", return_value="ghr_ok"),
        patch(
            "agent.dashboard.profiles.refresh_user_access_token",
            new_callable=AsyncMock,
            side_effect=GithubOAuthError(503, "github oauth temporarily unavailable"),
        ),
        patch(
            "agent.dashboard.profiles.delete_access_token",
            new_callable=AsyncMock,
        ) as mock_delete,
    ):
        token = await get_valid_access_token("octo")
    assert token == "still-usable"
    mock_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_valid_access_token_returns_stored_when_not_expiring() -> None:
    future = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
    record = {
        "encrypted_gh_token": "enc-access",
        "encrypted_gh_refresh_token": "enc-refresh",
        "token_expires_at": future,
    }
    with (
        patch(
            "agent.dashboard.profiles._get_value",
            new_callable=AsyncMock,
            return_value=record,
        ),
        patch("agent.dashboard.profiles._decrypt_access_token", return_value="still-good"),
        patch(
            "agent.dashboard.profiles.refresh_user_access_token",
            new_callable=AsyncMock,
        ) as mock_refresh,
    ):
        token = await get_valid_access_token("octo")
    assert token == "still-good"
    mock_refresh.assert_not_called()
