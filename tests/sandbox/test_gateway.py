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
from runtime_api import models as model
from runtime_api.gateway_auth import GatewayBearer
from runtime_api.models import OpenAIReasoning

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


async def test_gemini_sdk_uses_allowlisted_gateway_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: list[str] = []
    services: list[str | None] = []
    expected_path = "/gemini/v1beta/models/gemini-4-carbon:streamGenerateContent"

    async def handler(request: web.Request) -> web.Response:
        paths.append(request.path)
        services.append(request.headers.get(model.GATEWAY_SERVICE_HEADER))
        if request.path != expected_path:
            return web.json_response({"error": "path not allow-listed by gateway"}, status=501)
        return web.Response(
            content_type="text/event-stream",
            text='data: {"candidates":[{"content":{"parts":[{"text":"ok"}],'
            '"role":"model"},"finishReason":"STOP"}]}\n\n',
        )

    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "dummy")
    async with _http_server(handler) as base_url:
        monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", base_url)
        chat_model = model.build_chat_model(
            "google_genai:gemini-4-carbon",
            {"max_retries": 1, "max_output_tokens": 64000, "reasoning_effort": "medium"},
            use_gateway=True,
        )
        chunks = [chunk async for chunk in chat_model.astream("hi")]

    assert "".join(str(chunk.content) for chunk in chunks) == "ok"
    assert paths == [expected_path]
    assert services == ["robin"]


@pytest.mark.parametrize(
    ("model_id", "kwargs"),
    [
        ("openai:gpt-5.6-sol", {}),
        ("anthropic:claude-opus-5-5", {}),
        ("baseten:zai-org/GLM-5", {}),
        (
            "fireworks:accounts/fireworks/models/glm-5p2",
            {"model_kwargs": {"reasoning_effort": "high"}},
        ),
    ],
)
async def test_gateway_calls_name_robin_as_the_service(
    monkeypatch: pytest.MonkeyPatch, model_id: str, kwargs: dict[str, object]
) -> None:
    """Every gateway-routed provider sends the service header, keeping its own request settings."""
    requests: list[tuple[str | None, bytes]] = []

    async def handler(request: web.Request) -> web.Response:
        requests.append((request.headers.get(model.GATEWAY_SERVICE_HEADER), await request.read()))
        return web.json_response({"error": {"message": "stop"}}, status=400)

    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "dummy")
    async with _http_server(handler) as base_url:
        monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", base_url)
        chat_model = model.build_chat_model(
            model_id, {"max_retries": 0, **kwargs}, use_gateway=True
        )
        with pytest.raises(Exception):  # noqa: B017, PT011 - the stub always answers 400
            await chat_model.ainvoke("hi")

    assert [service for service, _ in requests] == ["robin"]
    if "model_kwargs" in kwargs:
        assert b'"reasoning_effort":"high"' in requests[0][1].replace(b" ", b"")


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


@pytest.mark.parametrize(
    ("model_id", "path_prefix"),
    [
        ("openai:gpt-6-astra", "/openai/v1/responses"),
        ("anthropic:claude-haiku-5-5", "/anthropic/v1/messages"),
        ("fireworks:accounts/fireworks/models/glm-5p2", "/fireworks/v1/chat/completions"),
        ("google_genai:gemini-3.5-flash", "/gemini/v1beta/models/gemini-3.5-flash:"),
    ],
)
async def test_gateway_bearer_sends_a_fresh_token_per_request(
    monkeypatch: pytest.MonkeyPatch, model_id: str, path_prefix: str
) -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append((request.path, {k.lower(): v for k, v in request.headers.items()}))
        return web.json_response({"error": {"message": "stop here"}}, status=401)

    tokens = iter(["person-token-1", "person-token-2"])

    async def token() -> str:
        return next(tokens)

    async with _http_server(handler) as base_url:
        monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", base_url)
        monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "lsv2_deployment_key")
        chat = model.make_model(
            model_id, use_gateway=True, gateway=GatewayBearer(token), max_retries=0
        )
        for _ in range(2):
            with pytest.raises(Exception):  # noqa: B017 - each SDK raises its own 401 type
                await chat.ainvoke([HumanMessage("hi")])

    assert [path.startswith(path_prefix) for path, _ in seen] == [True, True]
    assert [headers.get("authorization") for _, headers in seen] == [
        "Bearer person-token-1",
        "Bearer person-token-2",
    ]
    assert [headers.get(model.GATEWAY_SERVICE_HEADER.lower()) for _, headers in seen] == [
        "robin",
        "robin",
    ]
    assert not any("x-api-key" in h or "x-goog-api-key" in h for _, h in seen)
