from typing import Any
from unittest.mock import patch

from openswe.utils import model


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


def test_openai_gets_codex_context_window_profile_override() -> None:
    captured = _make_model("openai:gpt-6.1-sol")
    profile = captured["profile"]
    assert profile["max_input_tokens"] == 272_000
    assert profile["tool_calling"] is True


def test_explicit_timeout_wins() -> None:
    assert _make_model("openai:gpt-6.1-sol", timeout=30.0)["timeout"] == 30.0
