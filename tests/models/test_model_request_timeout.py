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
    captured, fake = _capture()
    with patch.object(model, "init_chat_model", fake):
        model.make_model(model_id, use_gateway=False, **kwargs)
    return captured


def test_openai_gets_models_dev_context_window() -> None:
    from agent.model_catalog import CATALOG

    captured = _make_model("openai:gpt-6.1-sol")
    profile = captured["profile"]
    assert profile["max_input_tokens"] == CATALOG["openai:gpt-6.1-sol"].limit.input
    assert profile["tool_calling"] is True


def test_explicit_timeout_wins() -> None:
    assert _make_model("openai:gpt-6.1-sol", timeout=30.0)["timeout"] == 30.0
