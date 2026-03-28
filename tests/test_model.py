from __future__ import annotations

from agent.utils import model as model_utils


def test_make_model_uses_responses_api_for_openai(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_init_chat_model(*, model: str, **kwargs: object) -> dict[str, object]:
        captured["model"] = model
        captured["kwargs"] = kwargs
        return {"model": model, "kwargs": kwargs}

    monkeypatch.setattr(model_utils, "init_chat_model", fake_init_chat_model)

    result = model_utils.make_model("openai:gpt-5", temperature=0)

    assert result["model"] == "openai:gpt-5"
    assert result["kwargs"]["use_responses_api"] is True
    assert result["kwargs"]["base_url"] == model_utils.OPENAI_RESPONSES_WS_BASE_URL


def test_make_model_leaves_non_openai_models_unchanged(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_init_chat_model(*, model: str, **kwargs: object) -> dict[str, object]:
        captured["model"] = model
        captured["kwargs"] = kwargs
        return {"model": model, "kwargs": kwargs}

    monkeypatch.setattr(model_utils, "init_chat_model", fake_init_chat_model)

    result = model_utils.make_model("anthropic:claude-opus-4-6", temperature=0)

    assert result["model"] == "anthropic:claude-opus-4-6"
    assert "use_responses_api" not in result["kwargs"]
    assert "base_url" not in result["kwargs"]
