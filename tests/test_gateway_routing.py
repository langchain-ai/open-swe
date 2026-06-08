from unittest.mock import patch

import pytest

from agent.utils import model as model_module
from agent.utils.model import make_model


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test_key")
    monkeypatch.delenv("LANGSMITH_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LANGSMITH_GATEWAY_DISABLED", raising=False)


def test_openai_routes_through_gateway() -> None:
    with patch.object(model_module, "init_chat_model") as init:
        make_model("openai:gpt-5.5")
    kwargs = init.call_args.kwargs
    assert kwargs["base_url"] == "https://gateway.smith.langchain.com/openai/v1"
    assert kwargs["api_key"] == "lsv2_test_key"
    assert kwargs["use_responses_api"] is True


def test_anthropic_routes_through_gateway() -> None:
    with patch.object(model_module, "init_chat_model") as init:
        make_model("anthropic:claude-opus-4-8")
    kwargs = init.call_args.kwargs
    assert kwargs["base_url"] == "https://gateway.smith.langchain.com/anthropic"
    assert kwargs["api_key"] == "lsv2_test_key"
    assert "use_responses_api" not in kwargs


def test_gemini_and_fireworks_route_through_gateway() -> None:
    with patch.object(model_module, "init_chat_model") as init:
        make_model("google_genai:gemini-3.5-flash")
    assert init.call_args.kwargs["base_url"] == "https://gateway.smith.langchain.com/gemini"

    with patch.object(model_module, "init_chat_model") as init:
        make_model("fireworks:accounts/fireworks/models/kimi-k2p6")
    assert init.call_args.kwargs["base_url"] == "https://gateway.smith.langchain.com/fireworks"


def test_custom_gateway_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", "https://gw.example.com/")
    with patch.object(model_module, "init_chat_model") as init:
        make_model("openai:gpt-5.5")
    assert init.call_args.kwargs["base_url"] == "https://gw.example.com/openai/v1"


def test_gateway_disabled_falls_back_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_GATEWAY_DISABLED", "true")
    with patch.object(model_module, "init_chat_model") as init:
        make_model("openai:gpt-5.5")
    kwargs = init.call_args.kwargs
    assert kwargs["base_url"] == model_module.OPENAI_RESPONSES_WS_BASE_URL
    assert "api_key" not in kwargs


def test_missing_api_key_falls_back_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY_PROD", raising=False)
    with patch.object(model_module, "init_chat_model") as init:
        make_model("anthropic:claude-opus-4-8")
    assert "base_url" not in init.call_args.kwargs
