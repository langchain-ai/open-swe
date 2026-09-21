from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.dashboard.agent_overrides import load_profile
from agent.dashboard.profiles import Profile, get_profile


@pytest.mark.asyncio
async def test_missing_profile_has_defaults_without_overriding_workspace_models() -> None:
    with patch("agent.dashboard.profiles.get_value", new_callable=AsyncMock, return_value=None):
        profile = await get_profile("alice")
    assert profile.dm_session_enabled is False
    assert profile.draft_prs is True
    assert profile.model_routing_enabled is None
    assert profile.default_model is None
    assert profile.model_dump(exclude_unset=True) == {}


@pytest.mark.asyncio
async def test_invalid_stored_profile_is_rejected_but_run_loading_falls_back() -> None:
    with patch("agent.dashboard.profiles.get_value", return_value={"dm_session_enabled": "true"}):
        with pytest.raises(ValidationError):
            await get_profile("alice")
    with patch(
        "agent.dashboard.agent_overrides.get_value", return_value={"dm_session_enabled": "true"}
    ):
        assert await load_profile("alice") == Profile()
