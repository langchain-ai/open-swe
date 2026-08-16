from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.dashboard.team_settings import (
    DEFAULT_TRANSCRIPTION_MODEL,
    TeamSettingsUpdate,
    get_team_transcription_model,
)


def test_transcription_model_defaults_to_recommended_model() -> None:
    assert TeamSettingsUpdate().transcription_model == DEFAULT_TRANSCRIPTION_MODEL
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(transcription_model="retired-model")


async def test_invalid_stored_transcription_model_uses_default() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"transcription_model": "retired-model"},
    ):
        assert await get_team_transcription_model() == DEFAULT_TRANSCRIPTION_MODEL
