"""Unit tests for LangSmith LLM Gateway routing (agent/utils/gateway.py + make_model)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from agent.utils import gateway, model

_GATEWAY_ENV_VARS = (
    "LANGSMITH_API_KEY",
    "LANGSMITH_API_KEY_PROD",
    "LANGSMITH_GATEWAY_ENABLED",
    "LANGSMITH_GATEWAY_BASE_URL",
    "LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES",
)


@pytest.fixture(autouse=True)
def _clean_gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test from a known env: no key, gateway off, default base URL."""
    for name in _GATEWAY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# --- gateway_overrides --------------------------------------------------------


def test_openai_overrides_use_chat_completions_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    overrides = gateway.gateway_overrides("openai:gpt-5.5")
    assert overrides == {
        "base_url": "https://gateway.smith.langchain.com/openai/v1",
        "api_key": "ls-key",
        "use_responses_api": False,
    }


def test_openai_overrides_responses_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES", "true")
    overrides = gateway.gateway_overrides("openai:gpt-5.5")
    assert overrides is not None
    assert overrides["use_responses_api"] is True


def test_anthropic_overrides_have_no_responses_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    overrides = gateway.gateway_overrides("anthropic:claude-opus-4-8")
    assert overrides == {
        "base_url": "https://gateway.smith.langchain.com/anthropic",
        "api_key": "ls-key",
    }


def test_fireworks_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    overrides = gateway.gateway_overrides("fireworks:accounts/fireworks/models/glm-5p2")
    assert overrides is not None
    assert overrides["base_url"] == "https://gateway.smith.langchain.com/fireworks/v1"


def test_google_genai_routes_to_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    overrides = gateway.gateway_overrides("google_genai:gemini-3.5-flash")
    assert overrides == {
        "base_url": "https://gateway.smith.langchain.com/gemini",
        "api_key": "ls-key",
    }


def test_unsupported_provider_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    # Vertex authenticates with a service account, not a bearer key, so it isn't routed.
    assert gateway.gateway_overrides("google_vertexai:gemini-2.5-pro") is None


def test_missing_api_key_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    assert gateway.gateway_overrides("openai:gpt-5.5") is None


def test_prod_key_used_as_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "ls-prod-key")
    overrides = gateway.gateway_overrides("anthropic:claude-opus-4-8")
    assert overrides is not None
    assert overrides["api_key"] == "ls-prod-key"


def test_base_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", "https://gw.internal.example.com/")
    overrides = gateway.gateway_overrides("anthropic:claude-opus-4-8")
    assert overrides is not None
    # Trailing slash is stripped, then the provider path is appended.
    assert overrides["base_url"] == "https://gw.internal.example.com/anthropic"


# --- resolve_gateway_enabled --------------------------------------------------


@pytest.mark.parametrize(
    ("team_value", "env_enabled", "expected"),
    [
        (True, False, True),  # team True wins over env off
        (False, True, False),  # team False wins over env on
        (None, True, True),  # unset inherits env on
        (None, False, False),  # unset inherits env off
    ],
)
def test_resolve_gateway_enabled_precedence(
    monkeypatch: pytest.MonkeyPatch,
    team_value: bool | None,
    env_enabled: bool,
    expected: bool,
) -> None:
    if env_enabled:
        monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "true")
    assert gateway.resolve_gateway_enabled(team_value) is expected


# --- make_model integration ---------------------------------------------------


def _capture_init_chat_model() -> tuple[dict[str, Any], Any]:
    """Patch init_chat_model to record the kwargs make_model builds."""
    captured: dict[str, Any] = {}

    def _fake(model: str, **kwargs: Any) -> str:
        captured["model"] = model
        captured.update(kwargs)
        return "MODEL"

    return captured, _fake


def test_make_model_direct_openai_uses_responses_websocket() -> None:
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model("openai:gpt-5.5", use_gateway=False)
    assert captured["base_url"] == model.OPENAI_RESPONSES_WS_BASE_URL
    assert captured["use_responses_api"] is True


def test_make_model_gateway_openai_replaces_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model("openai:gpt-5.5", use_gateway=True)
    assert captured["base_url"] == "https://gateway.smith.langchain.com/openai/v1"
    assert captured["use_responses_api"] is False
    assert captured["api_key"] == "ls-key"


def test_make_model_gateway_openai_converts_reasoning_for_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model(
            "openai:gpt-5.5",
            use_gateway=True,
            reasoning={"effort": "high", "summary": "auto"},
        )
    assert captured["use_responses_api"] is False
    assert captured["reasoning_effort"] == "high"
    assert "reasoning" not in captured


def test_make_model_gateway_openai_preserves_reasoning_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model(
            "openai:gpt-5.5",
            use_gateway=True,
            reasoning={"effort": "none"},
        )
    assert captured["use_responses_api"] is False
    assert captured["reasoning_effort"] == "none"
    assert "reasoning" not in captured


def test_make_model_gateway_openai_responses_optin_keeps_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES", "true")
    reasoning = {"effort": "high", "summary": "auto"}
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model("openai:gpt-5.5", use_gateway=True, reasoning=reasoning)
    assert captured["use_responses_api"] is True
    assert captured["reasoning"] == reasoning
    assert "reasoning_effort" not in captured


def test_make_model_gateway_follows_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "true")
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model("anthropic:claude-opus-4-8")  # use_gateway=None -> env default
    assert captured["base_url"] == "https://gateway.smith.langchain.com/anthropic"
    assert captured["api_key"] == "ls-key"


def test_make_model_gateway_google_genai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model("google_genai:gemini-3.5-flash", use_gateway=True)
    assert captured["base_url"] == "https://gateway.smith.langchain.com/gemini"
    assert captured["api_key"] == "ls-key"


def test_make_model_gateway_without_key_falls_back_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model("openai:gpt-5.5", use_gateway=True)  # no LangSmith key
    # No key -> overrides skipped -> the direct-provider websocket base stands.
    assert captured["base_url"] == model.OPENAI_RESPONSES_WS_BASE_URL
    assert captured["use_responses_api"] is True
    assert "api_key" not in captured
