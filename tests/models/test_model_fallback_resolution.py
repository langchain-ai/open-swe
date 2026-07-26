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
    model_profile_context_window,
    models_with_profile_context_windows,
    provider_fallback_pair,
)
from agent.dashboard.profiles import ProfileUpdate, normalize_profile_for_response
from agent.dashboard.team_settings import (
    TeamSettingsUpdate,
    get_team_default_model,
    normalize_team_settings_for_response,
)

STALE_ANTHROPIC = "anthropic:claude-opus-4-7"
SUPPORTED_ANTHROPIC = "anthropic:claude-opus-5"


def test_provider_fallback_preserves_provider_and_effort() -> None:
    assert provider_fallback_pair(STALE_ANTHROPIC, "xhigh") == (SUPPORTED_ANTHROPIC, "xhigh")


def test_provider_fallback_uses_default_effort_when_unsupported() -> None:
    assert provider_fallback_pair(STALE_ANTHROPIC, "bogus") == (SUPPORTED_ANTHROPIC, "high")
    assert provider_fallback_pair(STALE_ANTHROPIC, None) == (SUPPORTED_ANTHROPIC, "high")


def test_provider_fallback_resolves_openai_within_provider() -> None:
    fallback = provider_fallback_pair("openai:gpt-5-legacy", "low")
    assert fallback is not None
    model, effort = fallback
    assert model == "openai:gpt-5.5"
    assert effort == "low"


def test_supported_openai_models_include_gpt_5_5_and_gpt_5_6() -> None:
    assert "openai:gpt-5.5" in SUPPORTED_MODEL_IDS
    openai_options = [model for model in SUPPORTED_MODELS if model["id"].startswith("openai:")]
    assert [(model["id"], model["label"]) for model in openai_options] == [
        ("openai:gpt-5.5", "GPT-5.5"),
        ("openai:gpt-5.6-sol", "GPT-5.6 Sol"),
        ("openai:gpt-5.6-terra", "GPT-5.6 Terra"),
        ("openai:gpt-5.6-luna", "GPT-5.6 Luna"),
    ]


def test_supported_models_do_not_hardcode_context_windows() -> None:
    assert all("context_window" not in model for model in SUPPORTED_MODELS)


def test_model_profile_context_window_uses_langchain_profile() -> None:
    assert model_profile_context_window("openai:gpt-5.5") == 1_050_000


def test_models_with_profile_context_windows_enriches_copies() -> None:
    openai_models = [model for model in SUPPORTED_MODELS if model["id"].startswith("openai:")]
    enriched = models_with_profile_context_windows(openai_models)
    assert all("context_window" not in model for model in openai_models)
    assert {model["id"]: model.get("context_window") for model in enriched} == {
        "openai:gpt-5.5": 1_050_000,
        "openai:gpt-5.6-sol": 1_050_000,
        "openai:gpt-5.6-terra": 1_050_000,
        "openai:gpt-5.6-luna": 1_050_000,
    }


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


def test_profile_update_preserves_gpt_5_5_model() -> None:
    update = ProfileUpdate(default_model="openai:gpt-5.5", reasoning_effort="medium")
    update.validate_pairing()
    assert update.default_model == "openai:gpt-5.5"
    assert update.reasoning_effort == "medium"


def test_profile_update_preserves_gpt_5_5_subagent_model() -> None:
    update = ProfileUpdate(
        default_model="openai:gpt-5.6-terra",
        reasoning_effort="high",
        default_subagent_model="openai:gpt-5.5",
        subagent_reasoning_effort="low",
    )
    update.validate_pairing()
    assert update.default_subagent_model == "openai:gpt-5.5"
    assert update.subagent_reasoning_effort == "low"


def test_profile_response_preserves_gpt_5_5_models() -> None:
    profile = normalize_profile_for_response(
        {
            "default_model": "openai:gpt-5.5",
            "reasoning_effort": "medium",
            "default_subagent_model": "openai:gpt-5.5",
            "subagent_reasoning_effort": "low",
        }
    )
    assert profile["default_model"] == "openai:gpt-5.5"
    assert profile["reasoning_effort"] == "medium"
    assert profile["default_subagent_model"] == "openai:gpt-5.5"
    assert profile["subagent_reasoning_effort"] == "low"


def test_team_settings_update_preserves_gpt_5_5_models() -> None:
    update = TeamSettingsUpdate(
        default_agent_model="openai:gpt-5.6-sol",
        default_agent_reasoning_effort="medium",
        default_agent_subagent_model="openai:gpt-5.5",
        default_agent_subagent_reasoning_effort="medium",
        default_reviewer_model="openai:gpt-5.5",
        default_reviewer_reasoning_effort="medium",
        default_reviewer_subagent_model="openai:gpt-5.5",
        default_reviewer_subagent_reasoning_effort="low",
    )

    assert update.default_agent_subagent_model == "openai:gpt-5.5"
    assert update.default_reviewer_model == "openai:gpt-5.5"
    assert update.default_reviewer_subagent_model == "openai:gpt-5.5"


def test_team_settings_update_rejects_unknown_openai_model() -> None:
    with pytest.raises(ValueError, match="unsupported agent model"):
        TeamSettingsUpdate(
            default_agent_model="openai:gpt-5.6-slo",
            default_agent_reasoning_effort="medium",
        )


def test_team_settings_update_rejects_invalid_effort_for_gpt_5_5() -> None:
    with pytest.raises(ValueError, match="effort 'bogus' not supported"):
        TeamSettingsUpdate(
            default_agent_model="openai:gpt-5.5",
            default_agent_reasoning_effort="bogus",
        )


def test_team_settings_response_preserves_gpt_5_5_models() -> None:
    settings = normalize_team_settings_for_response(
        {
            "default_agent_subagent_model": "openai:gpt-5.5",
            "default_agent_subagent_reasoning_effort": "medium",
            "default_reviewer_model": "openai:gpt-5.5",
            "default_reviewer_reasoning_effort": "medium",
            "default_reviewer_subagent_model": "openai:gpt-5.5",
            "default_reviewer_subagent_reasoning_effort": "low",
        }
    )

    assert settings["default_agent_subagent_model"] == "openai:gpt-5.5"
    assert settings["default_reviewer_model"] == "openai:gpt-5.5"
    assert settings["default_reviewer_subagent_model"] == "openai:gpt-5.5"


def test_profile_update_rejects_unknown_provider() -> None:
    update = ProfileUpdate(default_model="mystery:model", reasoning_effort="high")
    with pytest.raises(ValueError, match="not supported"):
        update.validate_pairing()


def test_profile_without_model_defers_to_team_default() -> None:
    assert normalize_profile_overrides({"reasoning_effort": "high"}) == (None, None)


def test_profile_unknown_provider_defers_to_team_default() -> None:
    profile = {"default_model": "mystery:model", "reasoning_effort": "high"}
    assert normalize_profile_overrides(profile) == (None, None)


def test_global_default_is_gpt_5_5() -> None:
    model, _ = default_model_pair()
    assert model == DEFAULT_MODEL_ID == "openai:gpt-5.5"


def test_gate_fable_passthrough_when_enabled() -> None:
    assert gate_fable_model("anthropic:claude-fable-5", "high", fable_enabled=True) == (
        "anthropic:claude-fable-5",
        "high",
    )


def test_gate_fable_swaps_to_opus_when_disabled() -> None:
    assert gate_fable_model("anthropic:claude-fable-5", "high", fable_enabled=False) == (
        SUPPORTED_ANTHROPIC,
        "high",
    )


def test_gate_fable_leaves_non_fable_ids_alone() -> None:
    assert gate_fable_model("openai:gpt-5.6-sol", "high", fable_enabled=False) == (
        "openai:gpt-5.6-sol",
        "high",
    )


def test_fable_disabled_fallback_is_non_fable_anthropic() -> None:
    model, effort = fable_disabled_fallback("high")
    assert model == SUPPORTED_ANTHROPIC
    assert model not in FABLE_MODEL_IDS
    assert effort == "high"
