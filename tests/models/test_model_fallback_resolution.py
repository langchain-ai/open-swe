import runpy

import pytest

from agent.dashboard import options
from agent.dashboard.agent_overrides import normalize_profile_overrides
from agent.dashboard.options import (
    provider_fallback_pair,
)
from agent.dashboard.profiles import normalize_profile_for_response
from agent.dashboard.workspace_settings import (
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


def test_provider_fallback_preserves_provider_and_effort() -> None:
    assert provider_fallback_pair(STALE_ANTHROPIC, "xhigh") == (SUPPORTED_ANTHROPIC, "xhigh")


@pytest.mark.parametrize("model_id", ["unknown:model", "no-colon", "", None, 123])
def test_provider_fallback_returns_none_without_provider_match(model_id: object) -> None:
    assert provider_fallback_pair(model_id, "high") is None


def test_profile_response_and_override_defer_deprecated_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id, effort = DEPRECATED_GLM, "high"
    monkeypatch.setattr(options, "DEPRECATED_MODEL_IDS", {model_id})
    from agent.dashboard import profiles

    monkeypatch.setattr(profiles, "DEPRECATED_MODEL_IDS", {model_id})
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
    assert normalize_profile_overrides({"default_model": model_id, "reasoning_effort": effort}) == (
        None,
        None,
    )


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
