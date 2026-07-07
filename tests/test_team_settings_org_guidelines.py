from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.dashboard.team_settings import (
    ORG_GUIDELINES_MAX_CHARS,
    REVIEW_TRACING_PROJECT_MAX_CHARS,
    TeamSettingsUpdate,
    get_org_review_guidelines,
    get_team_default_model,
    get_team_review_tracing_project,
)

_AGENT_PAIR = ("anthropic:claude-opus-4-8", "high")
_CHAT_PAIR = ("google_genai:gemini-3.5-flash", "low")


def test_org_guidelines_blank_normalizes_to_none() -> None:
    assert TeamSettingsUpdate(org_guidelines="   ").org_guidelines is None
    assert TeamSettingsUpdate(org_guidelines=None).org_guidelines is None


def test_org_guidelines_trimmed() -> None:
    update = TeamSettingsUpdate(org_guidelines="  Flag CI gate removals.\n")
    assert update.org_guidelines == "Flag CI gate removals."


def test_org_guidelines_rejects_oversized() -> None:
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(org_guidelines="x" * (ORG_GUIDELINES_MAX_CHARS + 1))


def test_review_tracing_project_blank_normalizes_to_none() -> None:
    assert TeamSettingsUpdate(review_tracing_project="   ").review_tracing_project is None
    assert TeamSettingsUpdate(review_tracing_project=None).review_tracing_project is None


def test_review_tracing_project_trimmed() -> None:
    update = TeamSettingsUpdate(review_tracing_project="  pajuha\n")
    assert update.review_tracing_project == "pajuha"


def test_review_tracing_project_rejects_oversized() -> None:
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(review_tracing_project="x" * (REVIEW_TRACING_PROJECT_MAX_CHARS + 1))


@pytest.mark.asyncio
async def test_get_team_review_tracing_project_returns_trimmed_text() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"review_tracing_project": "  pajuha\n"},
    ):
        assert await get_team_review_tracing_project() == "pajuha"


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


def _settings(**overrides: object) -> dict[str, object]:
    base = {
        "default_agent_model": _AGENT_PAIR[0],
        "default_agent_reasoning_effort": _AGENT_PAIR[1],
        "default_chat_model": None,
        "default_chat_reasoning_effort": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_chat_default_inherits_agent_when_unset() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value=_settings(),
    ):
        assert await get_team_default_model("chat") == _AGENT_PAIR


@pytest.mark.asyncio
async def test_chat_default_uses_chat_model_when_set() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value=_settings(
            default_chat_model=_CHAT_PAIR[0],
            default_chat_reasoning_effort=_CHAT_PAIR[1],
        ),
    ):
        assert await get_team_default_model("chat") == _CHAT_PAIR


@pytest.mark.asyncio
async def test_chat_default_inherits_agent_when_chat_model_invalid() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value=_settings(
            default_chat_model="bogus:model",
            default_chat_reasoning_effort="high",
        ),
    ):
        assert await get_team_default_model("chat") == _AGENT_PAIR


def test_team_settings_update_accepts_chat_pair() -> None:
    update = TeamSettingsUpdate(
        default_chat_model=_CHAT_PAIR[0],
        default_chat_reasoning_effort=_CHAT_PAIR[1],
    )
    assert update.default_chat_model == _CHAT_PAIR[0]
    assert update.default_chat_reasoning_effort == _CHAT_PAIR[1]


def test_team_settings_update_rejects_chat_effort_without_model() -> None:
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(default_chat_reasoning_effort="high")


def test_team_settings_update_rejects_unsupported_chat_effort() -> None:
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(default_chat_model=_CHAT_PAIR[0], default_chat_reasoning_effort="max")
