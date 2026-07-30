from __future__ import annotations

from typing import Any
from unittest.mock import patch

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


def test_anthropic_gets_a_default_request_timeout() -> None:
    assert _make_model("anthropic:claude-opus-5")["timeout"] == (
        model.DEFAULT_REQUEST_TIMEOUT_SECONDS
    )


def test_explicit_timeout_wins() -> None:
    assert _make_model("openai:gpt-5.6-sol", timeout=30.0)["timeout"] == 30.0


def test_unknown_provider_gets_no_timeout() -> None:
    assert "timeout" not in _make_model("ollama:llama4")
