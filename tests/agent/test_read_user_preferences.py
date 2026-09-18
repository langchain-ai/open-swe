from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.profiles import Profile
from agent.tools.read_user_preferences import read_user_preferences


@pytest.mark.asyncio
async def test_public_thread_cannot_read_preferences() -> None:
    with (
        patch("agent.tools.read_user_preferences.private_credential_login", return_value=None),
        patch("agent.tools.read_user_preferences.get_profile", new_callable=AsyncMock) as read,
    ):
        assert (await read_user_preferences())["ok"] is False
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_reads_private_owner_preferences_with_defaults_without_identity() -> None:
    with (
        patch("agent.tools.read_user_preferences.private_credential_login", return_value="alice"),
        patch(
            "agent.tools.read_user_preferences.get_profile",
            return_value=Profile(email="private@example.com", default_repo="org/repo"),
        ) as read,
        patch(
            "agent.tools.read_user_preferences.get_user_preferences",
            return_value={"default_visibility": "private"},
        ),
    ):
        result = await read_user_preferences()
    read.assert_awaited_once_with("alice")
    assert result["profile"]["default_repo"] == "org/repo"
    assert result["profile"]["draft_prs"] is True
    assert "email" not in result["profile"]
    assert result["dashboard"]["default_visibility"] == "private"
