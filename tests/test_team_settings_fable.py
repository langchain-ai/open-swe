from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.dashboard.team_settings import TeamSettingsUpdate, get_team_fable_enabled

_FABLE = "anthropic:claude-fable-5"

# --- accessor: get_team_fable_enabled (async, patched store) ---


@pytest.mark.asyncio
async def test_fable_enabled_defaults_false_when_absent() -> None:
    # Legacy record with no fable_enabled key -> off.
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={},
    ):
        assert await get_team_fable_enabled() is False


@pytest.mark.asyncio
async def test_fable_enabled_true_when_set() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"fable_enabled": True},
    ):
        assert await get_team_fable_enabled() is True


@pytest.mark.asyncio
async def test_fable_enabled_false_for_non_bool_value() -> None:
    # Fail-closed: any non-bool (e.g. a stray string) resolves to False.
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"fable_enabled": "true"},
    ):
        assert await get_team_fable_enabled() is False


# --- validation: TeamSettingsUpdate (sync) ---


def test_update_defaults_fable_disabled() -> None:
    assert TeamSettingsUpdate().fable_enabled is False


def test_update_rejects_fable_model_when_disabled() -> None:
    # fable_enabled defaults to False; a Fable default model must be rejected.
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(
            default_agent_model=_FABLE,
            default_agent_reasoning_effort="high",
        )


def test_update_rejects_fable_in_any_role_when_disabled() -> None:
    # Same guard applies to every model field, e.g. review chat.
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(
            default_chat_model=_FABLE,
            default_chat_reasoning_effort="high",
        )


def test_update_accepts_fable_model_when_enabled() -> None:
    update = TeamSettingsUpdate(
        fable_enabled=True,
        default_agent_model=_FABLE,
        default_agent_reasoning_effort="high",
    )
    assert update.fable_enabled is True
    assert update.default_agent_model == _FABLE
