"""Unit tests for LangSmith LLM Gateway routing (agent/utils/gateway.py + make_model)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fireworks import AsyncFireworks
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from agent.utils import gateway, model

_GATEWAY_ENV_VARS = (
    "LANGSMITH_API_KEY",
    "LANGSMITH_API_KEY_PROD",
    "LANGSMITH_GATEWAY_API_KEY",
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


def test_openai_overrides_use_responses_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    overrides = gateway.gateway_overrides("openai:gpt-5.6-sol")
    assert overrides == {
        "base_url": "https://gateway.smith.langchain.com/openai/v1",
        "api_key": "ls-key",
        "use_responses_api": True,
    }


def test_openai_overrides_chat_completions_optout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES", "false")
    overrides = gateway.gateway_overrides("openai:gpt-5.6-sol")
    assert overrides is not None
    assert overrides["use_responses_api"] is False


async def test_openai_sdk_uses_gateway_responses_path() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-5.6-sol",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        chat_model = ChatOpenAI(
            model="gpt-5.6-sol",
            api_key="dummy",
            base_url="https://gateway.smith.langchain.com/openai/v1",
            use_responses_api=True,
            http_async_client=http_client,
            max_retries=0,
        )
        await chat_model.ainvoke([HumanMessage(content="hi")])
    finally:
        await http_client.aclose()

    assert len(requests) == 1
    assert requests[0].url.path == "/openai/v1/responses"


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
    assert overrides["base_url"] == "https://gateway.smith.langchain.com/fireworks"


async def test_fireworks_sdk_uses_allowlisted_gateway_path() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "accounts/fireworks/models/glm-5p2",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = AsyncFireworks(
            api_key="dummy",
            base_url="https://gateway.smith.langchain.com/fireworks",
            http_client=http_client,
            max_retries=0,
        )
        await client.chat.completions.create(
            model="accounts/fireworks/models/glm-5p2",
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        await http_client.aclose()

    assert len(requests) == 1
    assert requests[0].url.path == "/fireworks/v1/chat/completions"


async def test_fireworks_gateway_strips_legacy_function_call() -> None:
    """The serializer must not emit ``function_call`` after sanitization.

    Reproduces the production 400 — ``Extra inputs are not permitted, field:
    'messages[N].function_call'`` — by routing an ``AIMessage`` that carries the
    legacy ``function_call`` (alongside modern ``tool_calls``) through the
    Fireworks serializer toward the gateway. Without the sanitizer middleware
    the request body contains ``function_call``; with it, only ``tool_calls``
    survives.
    """
    import json

    from fireworks import AsyncFireworks
    from langchain_fireworks.chat_models import ChatFireworks

    from agent.middleware.sanitize_fireworks_messages import _sanitize_messages

    captured_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "accounts/fireworks/models/glm-5p2",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        chat_model = ChatFireworks(
            model="accounts/fireworks/models/glm-5p2",
            api_key="dummy",
            base_url="https://gateway.smith.langchain.com/fireworks",
            max_retries=0,
        )
        # Inject a mock-transport client so no real network call is made.
        mock_sdk = AsyncFireworks(
            api_key="dummy",
            base_url="https://gateway.smith.langchain.com/fireworks",
            http_client=http_client,
            max_retries=0,
        )
        chat_model._async_sdk_client = mock_sdk  # type: ignore[attr-defined]
        chat_model.async_client = mock_sdk.chat.completions  # type: ignore[attr-defined]

        ai_message = AIMessage(
            content="",
            tool_calls=[{"name": "read_file", "args": {"file_path": "/x"}, "id": "tc1"}],
            additional_kwargs={"function_call": {"name": "read_file", "arguments": "{}"}},
        )
        messages = [HumanMessage(content="hi"), ai_message]

        # Apply the sanitizer the same way the middleware stack does.
        _sanitize_messages(messages)

        await chat_model.ainvoke(messages)
        await mock_sdk.close()
    finally:
        await http_client.aclose()

    assert len(captured_bodies) == 1
    body = captured_bodies[0]
    for msg in body["messages"]:
        assert "function_call" not in msg, msg
    # The assistant message still carries tool_calls.
    assistant_msgs = [m for m in body["messages"] if m["role"] == "assistant"]
    assert assistant_msgs and "tool_calls" in assistant_msgs[0]


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
    assert gateway.gateway_overrides("openai:gpt-5.6-sol") is None


def test_prod_key_used_as_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "ls-prod-key")
    overrides = gateway.gateway_overrides("anthropic:claude-opus-4-8")
    assert overrides is not None
    assert overrides["api_key"] == "ls-prod-key"


def test_prod_key_preferred_over_platform_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-platform-key")
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "ls-prod-key")
    overrides = gateway.gateway_overrides("anthropic:claude-opus-4-8")
    assert overrides is not None
    assert overrides["api_key"] == "ls-prod-key"


def test_gateway_key_preferred_over_prod_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-platform-key")
    monkeypatch.setenv("LANGSMITH_API_KEY_PROD", "ls-prod-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "ls-gateway-key")
    overrides = gateway.gateway_overrides("anthropic:claude-opus-4-8")
    assert overrides is not None
    assert overrides["api_key"] == "ls-gateway-key"


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
        model.make_model("openai:gpt-5.6-sol", use_gateway=False)
    assert captured["base_url"] == model.OPENAI_RESPONSES_WS_BASE_URL
    assert captured["use_responses_api"] is True
    assert captured["store"] is False
    assert captured["include"] == ["reasoning.encrypted_content"]


def test_make_model_gateway_openai_replaces_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model("openai:gpt-5.6-sol", use_gateway=True)
    assert captured["base_url"] == "https://gateway.smith.langchain.com/openai/v1"
    assert captured["use_responses_api"] is True
    assert captured["store"] is False
    assert captured["include"] == ["reasoning.encrypted_content"]
    assert captured["api_key"] == "ls-key"


def test_make_model_gateway_openai_chat_completions_optout_converts_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES", "false")
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model(
            "openai:gpt-5.6-sol",
            use_gateway=True,
            reasoning={"effort": "high", "summary": "auto"},
        )
    assert captured["use_responses_api"] is False
    assert captured["reasoning_effort"] == "high"
    assert "reasoning" not in captured
    assert "include" not in captured
    assert "store" not in captured


def test_make_model_gateway_openai_preserves_reasoning_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model(
            "openai:gpt-5.6-sol",
            use_gateway=True,
            reasoning={"effort": "none"},
        )
    assert captured["use_responses_api"] is True
    assert captured["store"] is False
    assert captured["include"] == ["reasoning.encrypted_content"]
    assert captured["reasoning"] == {"effort": "none"}
    assert "reasoning_effort" not in captured


def test_make_model_gateway_openai_responses_keeps_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    reasoning = {"effort": "high", "summary": "auto"}
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model("openai:gpt-5.6-sol", use_gateway=True, reasoning=reasoning)
    assert captured["use_responses_api"] is True
    assert captured["store"] is False
    assert captured["include"] == ["reasoning.encrypted_content"]
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
        model.make_model("openai:gpt-5.6-sol", use_gateway=True)  # no LangSmith key
    # No key -> overrides skipped -> the direct-provider websocket base stands.
    assert captured["base_url"] == model.OPENAI_RESPONSES_WS_BASE_URL
    assert captured["use_responses_api"] is True
    assert captured["store"] is False
    assert captured["include"] == ["reasoning.encrypted_content"]
    assert "api_key" not in captured
