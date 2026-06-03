from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.agent_overrides import normalize_profile_overrides
from agent.dashboard.options import (
    DEFAULT_MODEL_ID,
    default_model_pair,
    provider_fallback_pair,
)
from agent.dashboard.team_settings import get_team_default_model

STALE_ANTHROPIC = "anthropic:claude-opus-4-7"
SUPPORTED_ANTHROPIC = "anthropic:claude-opus-4-8"


def test_provider_fallback_preserves_provider_and_effort() -> None:
    assert provider_fallback_pair(STALE_ANTHROPIC, "xhigh") == (SUPPORTED_ANTHROPIC, "xhigh")


def test_provider_fallback_uses_default_effort_when_unsupported() -> None:
    assert provider_fallback_pair(STALE_ANTHROPIC, "bogus") == (SUPPORTED_ANTHROPIC, "high")
    assert provider_fallback_pair(STALE_ANTHROPIC, None) == (SUPPORTED_ANTHROPIC, "high")


def test_provider_fallback_resolves_openai_within_provider() -> None:
    model, effort = provider_fallback_pair("openai:gpt-5-legacy", "low")
    assert model.startswith("openai:")
    assert effort == "low"


@pytest.mark.parametrize("model_id", ["unknown:model", "no-colon", "", None, 123])
def test_provider_fallback_returns_none_without_provider_match(model_id: object) -> None:
    assert provider_fallback_pair(model_id, "high") is None


@pytest.mark.asyncio
async def test_team_default_stale_anthropic_stays_on_provider() -> None:
    settings = {
        "default_agent_model": STALE_ANTHROPIC,
        "default_agent_reasoning_effort": "xhigh",
    }
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value=settings,
    ):
        assert await get_team_default_model("agent") == (SUPPORTED_ANTHROPIC, "xhigh")


@pytest.mark.asyncio
async def test_team_default_unknown_provider_falls_back_to_global() -> None:
    settings = {
        "default_reviewer_model": "mystery:model",
        "default_reviewer_reasoning_effort": "high",
    }
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value=settings,
    ):
        assert await get_team_default_model("reviewer") == default_model_pair()


def test_profile_stale_anthropic_upgrades_to_supported() -> None:
    profile = {"default_model": STALE_ANTHROPIC, "reasoning_effort": "high"}
    assert normalize_profile_overrides(profile) == (SUPPORTED_ANTHROPIC, "high")


def test_profile_without_model_defers_to_team_default() -> None:
    assert normalize_profile_overrides({"reasoning_effort": "high"}) == (None, None)


def test_profile_unknown_provider_defers_to_team_default() -> None:
    profile = {"default_model": "mystery:model", "reasoning_effort": "high"}
    assert normalize_profile_overrides(profile) == (None, None)


def test_global_default_is_supported() -> None:
    model, _ = default_model_pair()
    assert model == DEFAULT_MODEL_ID
