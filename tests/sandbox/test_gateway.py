"""Unit tests for LangSmith LLM Gateway routing (agent/utils/gateway.py + make_model)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import patch

import httpx2
import pytest
from aiohttp import web
from fireworks import AsyncFireworks
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from openswe.utils import model
from openswe.utils.model import OpenAIReasoning

_GATEWAY_ENV_VARS = (
    "LANGSMITH_API_KEY",
    "LANGSMITH_GATEWAY_API_KEY",
    "LANGSMITH_GATEWAY_ENABLED",
    "LANGSMITH_GATEWAY_BASE_URL",
    "LANGSMITH_GATEWAY_OPENAI_USE_RESPONSES",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
)


@asynccontextmanager
async def _http_server(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> AsyncIterator[str]:
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server = site._server
    assert server is not None
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest.fixture(autouse=True)
def _clean_gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test from a known env: no key, gateway off, default base URL."""
    for name in _GATEWAY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# --- gateway_overrides --------------------------------------------------------


async def test_openai_sdk_uses_gateway_responses_path() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
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

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    try:
        chat_model = ChatOpenAI(
            model="gpt-5.6-sol",
            api_key=SecretStr("dummy"),
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


async def test_fireworks_sdk_uses_allowlisted_gateway_path() -> None:
    paths: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        paths.append(request.path)
        return web.json_response(
            {
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
            }
        )

    async with _http_server(handler) as base_url:
        client = AsyncFireworks(
            api_key="dummy",
            base_url=f"{base_url}/fireworks",
            max_retries=0,
        )
        try:
            await client.chat.completions.create(
                model="accounts/fireworks/models/glm-5p2",
                messages=[{"role": "user", "content": "hi"}],
            )
        finally:
            await client.close()

    assert paths == ["/fireworks/v1/chat/completions"]


# --- resolve_gateway_enabled --------------------------------------------------


# --- make_model integration ---------------------------------------------------


def _capture_init_chat_model() -> tuple[dict[str, Any], Any]:
    """Patch init_chat_model to record the kwargs make_model builds."""
    captured: dict[str, Any] = {}

    def _fake(model: str, **kwargs: Any) -> str:
        captured["model"] = model
        captured.update(kwargs)
        return "MODEL"

    return captured, _fake


def test_make_model_openai_base_url_precedes_legacy_api_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://primary-proxy.example/v1")
    monkeypatch.setenv("OPENAI_API_BASE", "https://legacy-proxy.example/v1")
    captured, fake = _capture_init_chat_model()
    with patch.object(model, "init_chat_model", fake):
        model.make_model("openai:gpt-5.6-sol", use_gateway=False)
    assert captured["base_url"] == "https://primary-proxy.example/v1"


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
            reasoning=cast(OpenAIReasoning, {"effort": "high", "summary": "auto"}),
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
