"""Tests for self-service mapping completion in the OAuth callback."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent.dashboard import oauth, routes


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")


@pytest.mark.asyncio
async def test_completion_uses_link_token_slack_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_upsert = AsyncMock()
    monkeypatch.setattr(routes, "get_mapping", AsyncMock(return_value=None))
    monkeypatch.setattr(routes, "upsert_mapping", mock_upsert)
    link = oauth.issue_account_link(slack_user_id="U999", work_email="slack@x.com")

    await routes._complete_account_mapping("octo", "gh@x.com", link)

    mock_upsert.assert_awaited_once()
    kwargs = mock_upsert.await_args.kwargs
    assert kwargs["github_login"] == "octo"
    # Slack work email from the link token wins over the GitHub email.
    assert kwargs["work_email"] == "slack@x.com"
    assert kwargs["slack_user_id"] == "U999"
    assert kwargs["source"] == "self"


@pytest.mark.asyncio
async def test_completion_preserves_existing_slack_email(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_upsert = AsyncMock()
    monkeypatch.setattr(
        routes,
        "get_mapping",
        AsyncMock(
            return_value={
                "github_login": "octo",
                "work_email": "slack@x.com",
                "slack_user_id": "U999",
            }
        ),
    )
    monkeypatch.setattr(routes, "upsert_mapping", mock_upsert)

    await routes._complete_account_mapping("octo", "gh@x.com", None)

    kwargs = mock_upsert.await_args.kwargs
    assert kwargs["work_email"] == "slack@x.com"
    assert kwargs["slack_user_id"] is None


@pytest.mark.asyncio
async def test_completion_falls_back_to_github_email(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_upsert = AsyncMock()
    monkeypatch.setattr(routes, "get_mapping", AsyncMock(return_value=None))
    monkeypatch.setattr(routes, "upsert_mapping", mock_upsert)

    await routes._complete_account_mapping("octo", "gh@x.com", None)

    kwargs = mock_upsert.await_args.kwargs
    assert kwargs["work_email"] == "gh@x.com"
    assert kwargs["slack_user_id"] is None
    assert kwargs["source"] == "self"


@pytest.mark.asyncio
async def test_completion_noop_without_any_email(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_upsert = AsyncMock()
    monkeypatch.setattr(routes, "get_mapping", AsyncMock(return_value=None))
    monkeypatch.setattr(routes, "upsert_mapping", mock_upsert)

    await routes._complete_account_mapping("octo", None, None)

    mock_upsert.assert_not_awaited()
