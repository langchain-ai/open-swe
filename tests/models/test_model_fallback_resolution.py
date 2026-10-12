import runpy

import pytest

from openswe.dashboard import options
from openswe.dashboard.options import (
    FABLE_MODEL_IDS,
    fable_disabled_fallback,
    provider_fallback_pair,
)
from openswe.dashboard.profiles import normalize_profile_for_response
from openswe.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
)

STALE_ANTHROPIC = "anthropic:claude-opus-5"
SUPPORTED_ANTHROPIC = "anthropic:claude-opus-5-5"
SUPPORTED_OPENAI = "openai:gpt-6.1-sol"
SUPPORTED_ASTRA = "openai:gpt-6-astra"
SUPPORTED_KIMI = "fireworks:accounts/fireworks/models/kimi-k3"
DEPRECATED_ANTHROPIC = "anthropic:claude-opus-4-8"
DEPRECATED_OPENAI = "openai:gpt-5.5"
DEPRECATED_GLM = "fireworks:accounts/fireworks/models/glm-5p2"
FABLE = "anthropic:claude-fable-5-1"


def test_provider_fallback_preserves_provider_and_effort() -> None:
    assert provider_fallback_pair(STALE_ANTHROPIC, "xhigh") == (SUPPORTED_ANTHROPIC, "xhigh")


@pytest.mark.parametrize("model_id", ["unknown:model", "no-colon", "", None, 123])
def test_provider_fallback_returns_none_without_provider_match(model_id: object) -> None:
    assert provider_fallback_pair(model_id, "high") is None


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


def test_workspace_settings_update_rejects_invalid_effort_for_openai_model() -> None:
    with pytest.raises(ValueError, match="effort 'bogus' not supported"):
        WorkspaceSettingsUpdate(
            default_agent_model=SUPPORTED_OPENAI,
            default_agent_reasoning_effort="bogus",
        )


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


def test_fable_disabled_fallback_is_non_fable_anthropic() -> None:
    model, effort = fable_disabled_fallback("high")
    assert model == SUPPORTED_ANTHROPIC
    assert model not in FABLE_MODEL_IDS
    assert effort == "high"
