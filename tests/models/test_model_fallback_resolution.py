import runpy
from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard import options
from agent.dashboard.agent_overrides import normalize_profile_overrides
from agent.dashboard.options import (
    FABLE_MODEL_IDS,
    SUPPORTED_MODEL_IDS,
    SUPPORTED_MODELS,
    default_model_pair,
    fable_disabled_fallback,
    gate_fable_model,
    is_deprecated_model,
    model_profile_context_window,
    models_with_profile_context_windows,
    normalize_model_choice,
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
SUPPORTED_OPENAI = "openai:gpt-5.6-sol"
SUPPORTED_ASTRA = "openai:gpt-6-astra"
SUPPORTED_KIMI = "fireworks:accounts/fireworks/models/kimi-k3"
DEPRECATED_ANTHROPIC = "anthropic:claude-opus-4-8"
DEPRECATED_OPENAI = "openai:gpt-5.5"
DEPRECATED_GLM = "fireworks:accounts/fireworks/models/glm-5p2"
SUPPORTED_GLM = "fireworks:accounts/fireworks/models/glm-5p3"
FABLE = "anthropic:claude-fable-5-1"


def test_provider_fallback_preserves_provider_and_effort() -> None:
    assert provider_fallback_pair(STALE_ANTHROPIC, "xhigh") == (SUPPORTED_ANTHROPIC, "xhigh")


def test_provider_fallback_uses_default_effort_when_unsupported() -> None:
    assert provider_fallback_pair(STALE_ANTHROPIC, "bogus") == (SUPPORTED_ANTHROPIC, "high")
    assert provider_fallback_pair(STALE_ANTHROPIC, None) == (SUPPORTED_ANTHROPIC, "high")


def test_provider_fallback_resolves_openai_within_provider() -> None:
    fallback = provider_fallback_pair("openai:gpt-5-legacy", "low")
    assert fallback is not None
    model, effort = fallback
    assert model == SUPPORTED_ASTRA
    assert effort == "low"


def test_supported_openai_models() -> None:
    openai_options = [model for model in SUPPORTED_MODELS if model["id"].startswith("openai:")]
    assert [(model["id"], model["label"]) for model in openai_options] == [
        ("openai:gpt-6-astra", "GPT-6 Astra"),
        ("openai:gpt-5.6-sol", "GPT-5.6 Sol"),
        ("openai:gpt-5.6-terra", "GPT-5.6 Terra"),
        ("openai:gpt-5.6-luna", "GPT-5.6 Luna"),
    ]


@pytest.mark.parametrize("model_id", [DEPRECATED_OPENAI, DEPRECATED_ANTHROPIC, DEPRECATED_GLM])
def test_deprecated_models_are_no_longer_selectable(model_id: str) -> None:
    assert model_id not in SUPPORTED_MODEL_IDS
    assert all(model["id"] != model_id for model in SUPPORTED_MODELS)
    assert is_deprecated_model(model_id)


def test_fireworks_glm_5_3_replaces_glm_5_2() -> None:
    assert any(
        model["id"] == SUPPORTED_GLM and model["label"] == "GLM 5.3" for model in SUPPORTED_MODELS
    )


def test_deprecated_models_defer_to_defaults() -> None:
    for model_id in (DEPRECATED_OPENAI, DEPRECATED_ANTHROPIC, DEPRECATED_GLM):
        assert normalize_model_choice(model_id, "high") == (None, None)
        assert provider_fallback_pair(model_id, "high") is None
    assert normalize_model_choice("mystery:model", "high") == (None, None)


def test_supported_models_do_not_hardcode_context_windows() -> None:
    assert all("context_window" not in model for model in SUPPORTED_MODELS)


def test_model_profile_context_window_uses_codex_override() -> None:
    assert model_profile_context_window(SUPPORTED_ASTRA) == 1_050_000


def test_model_profile_context_window_uses_fireworks_profile_for_kimi_k3() -> None:
    assert model_profile_context_window(SUPPORTED_KIMI) == 1_048_576


def test_models_with_profile_context_windows_enriches_copies() -> None:
    models = [
        model
        for model in SUPPORTED_MODELS
        if model["id"].startswith("openai:") or model["id"] == SUPPORTED_KIMI
    ]
    enriched = models_with_profile_context_windows(models)
    assert all("context_window" not in model for model in models)
    assert {model["id"]: model.get("context_window") for model in enriched} == {
        "openai:gpt-6-astra": 1_050_000,
        "openai:gpt-5.6-sol": 272_000,
        "openai:gpt-5.6-terra": 272_000,
        "openai:gpt-5.6-luna": 272_000,
        SUPPORTED_KIMI: 1_048_576,
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


def test_profile_update_defaults_draft_prs_to_none_for_legacy_clients() -> None:
    update = ProfileUpdate(default_model=SUPPORTED_OPENAI, reasoning_effort="medium")

    assert update.draft_prs is None


def test_profile_response_and_override_defer_deprecated_models() -> None:
    profile = normalize_profile_for_response(
        {
            "default_model": DEPRECATED_GLM,
            "reasoning_effort": "high",
            "default_subagent_model": DEPRECATED_OPENAI,
            "subagent_reasoning_effort": "low",
        }
    )
    assert "default_model" not in profile
    assert "reasoning_effort" not in profile
    assert "default_subagent_model" not in profile
    assert "subagent_reasoning_effort" not in profile
    assert normalize_profile_overrides(
        {"default_model": DEPRECATED_GLM, "reasoning_effort": "high"}
    ) == (None, None)


def test_team_settings_update_rejects_unknown_openai_model() -> None:
    with pytest.raises(ValueError, match="unsupported agent model"):
        TeamSettingsUpdate(
            default_agent_model="openai:gpt-5.6-slo",
            default_agent_reasoning_effort="medium",
        )


def test_team_settings_update_rejects_invalid_effort_for_openai_model() -> None:
    with pytest.raises(ValueError, match="effort 'bogus' not supported"):
        TeamSettingsUpdate(
            default_agent_model=SUPPORTED_OPENAI,
            default_agent_reasoning_effort="bogus",
        )


def test_team_settings_response_defers_deprecated_models() -> None:
    settings = normalize_team_settings_for_response(
        {
            "default_agent_model": DEPRECATED_GLM,
            "default_agent_reasoning_effort": "high",
        }
    )

    assert settings["default_agent_model"] is None
    assert settings["default_agent_reasoning_effort"] is None


def test_fable_cannot_be_saved_as_default() -> None:
    profile = ProfileUpdate(default_model=FABLE, reasoning_effort="high")
    with pytest.raises(ValueError, match="cannot be a default model"):
        profile.validate_pairing()
    with pytest.raises(ValueError, match="cannot be a default model"):
        TeamSettingsUpdate(
            fable_enabled=True,
            default_agent_model=FABLE,
            default_agent_reasoning_effort="high",
        )


def test_profile_update_rejects_unknown_provider() -> None:
    update = ProfileUpdate(default_model="mystery:model", reasoning_effort="high")
    with pytest.raises(ValueError, match="not supported"):
        update.validate_pairing()


def test_profile_without_model_defers_to_team_default() -> None:
    assert normalize_profile_overrides({"reasoning_effort": "high"}) == (None, None)


def test_profile_unknown_provider_defers_to_team_default() -> None:
    profile = {"default_model": "mystery:model", "reasoning_effort": "high"}
    assert normalize_profile_overrides(profile) == (None, None)


@pytest.mark.parametrize(
    ("anthropic_key", "openai_key", "expected"),
    [
        ("key", "", SUPPORTED_ANTHROPIC),
        ("key", "key", SUPPORTED_OPENAI),
        ("", "", SUPPORTED_OPENAI),
    ],
)
def test_global_default_matches_available_credentials(
    monkeypatch: pytest.MonkeyPatch, anthropic_key: str, openai_key: str, expected: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_key)
    monkeypatch.setenv("OPENAI_API_KEY", openai_key)
    defaults = runpy.run_path(options.__file__)
    assert defaults["default_model_pair"]() == (expected, "medium")
    assert defaults["default_vision_model_pair"]() == (expected, "medium")


def test_gate_fable_passthrough_when_enabled() -> None:
    assert gate_fable_model("anthropic:claude-fable-5-1", "high", fable_enabled=True) == (
        "anthropic:claude-fable-5-1",
        "high",
    )


def test_gate_fable_swaps_to_opus_when_disabled() -> None:
    assert gate_fable_model("anthropic:claude-fable-5-1", "high", fable_enabled=False) == (
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
