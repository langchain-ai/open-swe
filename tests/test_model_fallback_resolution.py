from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.agent_overrides import normalize_profile_overrides
from agent.dashboard.options import (
    DEFAULT_MODEL_ID,
    FABLE_MODEL_IDS,
    SUPPORTED_MODEL_IDS,
    SUPPORTED_MODELS,
    default_model_pair,
    fable_disabled_fallback,
    gate_fable_model,
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
    assert model == "openai:gpt-5.6-sol"
    assert effort == "low"


def test_supported_openai_models_replace_gpt_5_5() -> None:
    assert "openai:gpt-5.5" not in SUPPORTED_MODEL_IDS
    openai_options = [model for model in SUPPORTED_MODELS if model["id"].startswith("openai:")]
    assert [(model["id"], model["label"]) for model in openai_options] == [
        ("openai:gpt-5.6-sol", "GPT-5.6 Sol"),
        ("openai:gpt-5.6-terra", "GPT-5.6 Terra"),
        ("openai:gpt-5.6-luna", "GPT-5.6 Luna"),
    ]


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


def test_gate_fable_passthrough_when_enabled() -> None:
    assert gate_fable_model("anthropic:claude-fable-5", "high", fable_enabled=True) == (
        "anthropic:claude-fable-5",
        "high",
    )


def test_gate_fable_swaps_to_opus_when_disabled() -> None:
    assert gate_fable_model("anthropic:claude-fable-5", "high", fable_enabled=False) == (
        "anthropic:claude-opus-4-8",
        "high",
    )


def test_gate_fable_leaves_non_fable_ids_alone() -> None:
    assert gate_fable_model("openai:gpt-5.6-sol", "high", fable_enabled=False) == (
        "openai:gpt-5.6-sol",
        "high",
    )


def test_fable_disabled_fallback_is_non_fable_anthropic() -> None:
    model, effort = fable_disabled_fallback("high")
    assert model == "anthropic:claude-opus-4-8"
    assert model not in FABLE_MODEL_IDS
    assert effort == "high"
