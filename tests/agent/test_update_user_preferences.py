from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.profiles import Profile
from agent.tools.update_user_preferences import update_user_preferences


@pytest.mark.asyncio
async def test_public_thread_cannot_write_preferences() -> None:
    with patch("agent.tools.update_user_preferences.private_credential_login", return_value=None):
        result = await update_user_preferences({"dm_session_enabled": True})
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_update_preserves_unspecified_preferences() -> None:
    with (
        patch("agent.tools.update_user_preferences.private_credential_login", return_value="alice"),
        patch(
            "agent.tools.update_user_preferences.get_profile",
            return_value=Profile.model_validate({"draft_prs": False} or {}),
        ),
        patch("agent.tools.update_user_preferences.upsert_profile", new_callable=AsyncMock) as save,
    ):
        result = await update_user_preferences({"dm_session_enabled": True})
    assert result["ok"] is True
    assert save.await_args.args[0] == "alice"
    assert save.await_args.args[2].dm_session_enabled is True
    assert save.await_args.args[2].draft_prs is False


@pytest.mark.asyncio
async def test_cannot_write_identity_or_arbitrary_settings() -> None:
    with patch(
        "agent.tools.update_user_preferences.private_credential_login", return_value="alice"
    ):
        result = await update_user_preferences({"login": "bob"})
    assert result["ok"] is False
