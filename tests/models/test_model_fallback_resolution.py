import runpy

import pytest

from openswe.dashboard import options
from openswe.dashboard.options import (
    FABLE_MODEL_IDS,
    fable_disabled_fallback,
)
from openswe.dashboard.profiles import normalize_profile_for_response
from openswe.dashboard.settings import (
    Settings,
    SettingsUpdate,
    _set_fields,
    normalize_settings_for_response,
)

SUPPORTED_ANTHROPIC = "anthropic:claude-opus-5-5"
SUPPORTED_OPENAI = "openai:gpt-6.1-sol"
DEPRECATED_GLM = "fireworks:accounts/fireworks/models/glm-5p2"


@pytest.mark.parametrize(
    ("model_id", "effort"),
    [
        (DEPRECATED_GLM, "high"),
        ("anthropic:claude-sonnet-5", "high"),
        ("anthropic:claude-haiku-4-5", "none"),
    ],
)
def test_profile_response_and_override_defer_deprecated_models(model_id: str, effort: str) -> None:
    profile = normalize_profile_for_response(
        {
            "default_model": model_id,
            "reasoning_effort": effort,
            "default_subagent_model": model_id,
            "subagent_reasoning_effort": effort,
        }
    )
    assert "default_model" not in profile
    assert "reasoning_effort" not in profile
    assert "default_subagent_model" not in profile
    assert "subagent_reasoning_effort" not in profile


def test_global_settings_update_rejects_invalid_effort_for_openai_model() -> None:
    with pytest.raises(ValueError, match="effort 'bogus' not supported"):
        SettingsUpdate(
            default_agent_model=SUPPORTED_OPENAI,
            default_agent_reasoning_effort="bogus",
        )


@pytest.mark.parametrize(
    ("anthropic_key", "openai_key", "expected"),
    [
        ("key", "", SUPPORTED_OPENAI),
        ("key", "key", SUPPORTED_OPENAI),
        ("", "", SUPPORTED_OPENAI),
    ],
)
def test_global_default_ignores_direct_provider_credentials(
    monkeypatch: pytest.MonkeyPatch, anthropic_key: str, openai_key: str, expected: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_key)
    monkeypatch.setenv("OPENAI_API_KEY", openai_key)
    defaults = runpy.run_path(options.__file__)
    assert defaults["default_model_pair"]() == (expected, "medium")
    assert defaults["default_vision_model_pair"]() == (expected, "medium")


def test_fable_disabled_fallback_is_non_fable_anthropic() -> None:
    model, effort = fable_disabled_fallback("high")
    assert model == SUPPORTED_ANTHROPIC
    assert model not in FABLE_MODEL_IDS
    assert effort == "high"


def test_unrecognized_stored_settings_fall_back_to_default() -> None:
    stored = {
        "default_agent_model": "openai:gpt-6-astra",
        "default_agent_reasoning_effort": "low",
        "default_agent_routing_performance_model": "anthropic:claude-fable-5-1",
        "default_agent_routing_performance_reasoning_effort": "high",
    }
    normalized = normalize_settings_for_response(stored)
    assert normalized["default_agent_model"] is None
    assert normalized["default_agent_reasoning_effort"] is None
    assert normalized["default_agent_routing_performance_model"] is None
    assert normalized["default_agent_routing_performance_reasoning_effort"] is None

    settings = Settings(stored)
    assert settings.default_model("agent") == options.default_model_pair()
    assert settings.agent_routing_models["performance"] == options.default_model_pair()

    update = SettingsUpdate.model_validate({**_set_fields(stored), "review_draft_prs": True})
    assert update.default_agent_model is None
    assert update.review_draft_prs is True


def test_an_unknown_model_is_rejected_rather_than_remapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="unsupported agent model"):
        SettingsUpdate(
            default_agent_model="openai:gpt-6.1-slo", default_agent_reasoning_effort="high"
        )
    monkeypatch.setenv("LLM_MODEL_ID", "openai:gpt-6.1-slo")
    with pytest.raises(ValueError, match="Unsupported default LLM_MODEL_ID"):
        options.default_model_pair()
