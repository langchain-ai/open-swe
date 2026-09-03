from typing import Any
from unittest.mock import patch

import pytest

from agent.utils import model


def _capture() -> tuple[dict[str, Any], Any]:
    captured: dict[str, Any] = {}

    def _fake(model: str, **kwargs: Any) -> str:
        captured["model"] = model
        captured.update(kwargs)
        return "MODEL"

    return captured, _fake


def _make_model(model_id: str, **kwargs: Any) -> dict[str, Any]:
    model._MODEL_CACHE.clear()
    captured, fake = _capture()
    with patch.object(model, "init_chat_model", fake):
        model.make_model(model_id, use_gateway=False, **kwargs)
    return captured


def test_openai_gets_a_default_request_timeout() -> None:
    captured = _make_model("openai:gpt-5.6-sol")
    assert captured["timeout"] == model.DEFAULT_REQUEST_TIMEOUT_SECONDS
    assert captured["max_retries"] == model.DEFAULT_MAX_RETRIES


def test_openai_gets_codex_context_window_profile_override() -> None:
    captured = _make_model("openai:gpt-5.6-sol")
    profile = captured["profile"]
    assert profile["max_input_tokens"] == 272_000
    assert profile["tool_calling"] is True


def test_anthropic_gets_a_default_request_timeout() -> None:
    captured = _make_model("anthropic:claude-opus-5")
    assert captured["timeout"] == model.DEFAULT_REQUEST_TIMEOUT_SECONDS
    assert "profile" not in captured


def test_explicit_timeout_wins() -> None:
    assert _make_model("openai:gpt-5.6-sol", timeout=30.0)["timeout"] == 30.0


def test_unknown_provider_gets_no_timeout() -> None:
    assert "timeout" not in _make_model("ollama:llama4")


def test_openai_base_url_can_use_chat_completions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("OPENAI_USE_RESPONSES_API", "false")

    captured = _make_model("openai:compatible-model")

    assert captured["base_url"] == "https://gateway.example/v1"
    assert captured["use_responses_api"] is False
