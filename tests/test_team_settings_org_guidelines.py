from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.dashboard.team_settings import (
    ORG_GUIDELINES_MAX_CHARS,
    TeamSettingsUpdate,
    get_org_review_guidelines,
)


def test_org_guidelines_blank_normalizes_to_none() -> None:
    assert TeamSettingsUpdate(org_guidelines="   ").org_guidelines is None
    assert TeamSettingsUpdate(org_guidelines=None).org_guidelines is None


def test_org_guidelines_trimmed() -> None:
    update = TeamSettingsUpdate(org_guidelines="  Flag CI gate removals.\n")
    assert update.org_guidelines == "Flag CI gate removals."


def test_org_guidelines_rejects_oversized() -> None:
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(org_guidelines="x" * (ORG_GUIDELINES_MAX_CHARS + 1))


@pytest.mark.asyncio
async def test_get_org_review_guidelines_returns_trimmed_text() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"org_guidelines": "  Always check auth.\n"},
    ):
        assert await get_org_review_guidelines() == "Always check auth."


@pytest.mark.asyncio
async def test_get_org_review_guidelines_returns_none_when_unset() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"org_guidelines": None},
    ):
        assert await get_org_review_guidelines() is None
