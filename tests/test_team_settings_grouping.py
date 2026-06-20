from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.dashboard.team_settings import (
    TeamSettingsUpdate,
    get_team_default_grouping_model,
)

_REVIEWER_SUBAGENT_PAIR = ("openai:gpt-5.5", "low")
_GROUPING_PAIR = ("google_genai:gemini-3.5-flash", "low")


def _settings(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "default_reviewer_subagent_model": _REVIEWER_SUBAGENT_PAIR[0],
        "default_reviewer_subagent_reasoning_effort": _REVIEWER_SUBAGENT_PAIR[1],
        "default_grouping_model": None,
        "default_grouping_reasoning_effort": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_grouping_inherits_reviewer_subagent_when_unset() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value=_settings(),
    ):
        assert await get_team_default_grouping_model() == _REVIEWER_SUBAGENT_PAIR


@pytest.mark.asyncio
async def test_grouping_uses_configured_model_when_set() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value=_settings(
            default_grouping_model=_GROUPING_PAIR[0],
            default_grouping_reasoning_effort=_GROUPING_PAIR[1],
        ),
    ):
        assert await get_team_default_grouping_model() == _GROUPING_PAIR


@pytest.mark.asyncio
async def test_grouping_inherits_when_configured_model_invalid() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value=_settings(
            default_grouping_model="bogus:model",
            default_grouping_reasoning_effort="high",
        ),
    ):
        assert await get_team_default_grouping_model() == _REVIEWER_SUBAGENT_PAIR


def test_team_settings_update_accepts_grouping_pair() -> None:
    update = TeamSettingsUpdate(
        default_grouping_model=_GROUPING_PAIR[0],
        default_grouping_reasoning_effort=_GROUPING_PAIR[1],
    )
    assert update.default_grouping_model == _GROUPING_PAIR[0]
    assert update.default_grouping_reasoning_effort == _GROUPING_PAIR[1]


def test_team_settings_update_rejects_grouping_effort_without_model() -> None:
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(default_grouping_reasoning_effort="high")


def test_team_settings_update_rejects_unsupported_grouping_effort() -> None:
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(
            default_grouping_model=_GROUPING_PAIR[0],
            default_grouping_reasoning_effort="max",
        )
