import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import cast

import httpx
import pytest
from fireworks import AsyncFireworks
from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from langgraph.runtime import ExecutionInfo, Runtime
from pydantic import SecretStr
from rt.backend import Run, RunContext
from rt.cache_affinity import cache_affinity
from rt.middleware import BackendRunMiddleware, ModelCall
from runtime_api.catalog import Catalog, ModelSpec


def _request(model: BaseChatModel, thread: str | None) -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=[HumanMessage(thread or "hello")],
        runtime=Runtime(
            execution_info=ExecutionInfo(
                checkpoint_id="", checkpoint_ns="", task_id="", thread_id=thread
            )
        ),
    )


async def _affinity(
    request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
) -> ModelResponse:
    openai, fireworks = cache_affinity()

    async def inner(selected: ModelRequest) -> ModelResponse:
        response = await fireworks.awrap_model_call(selected, handler)
        assert isinstance(response, ModelResponse)
        return response

    response = await openai.awrap_model_call(request, inner)
    assert isinstance(response, ModelResponse)
    return response


@pytest.mark.parametrize("provider", ["openai", "fireworks"])
async def test_shared_models_send_thread_local_affinity_on_the_wire(provider: str) -> None:
    sent: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        sent.append(request)
        body = (
            {"id": "resp_test", "object": "response", "status": "completed", "output": []}
            if provider == "openai"
            else {
                "id": "chat_test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
            }
        )
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        model = (
            ChatOpenAI(
                model="test",
                api_key=SecretStr("test"),
                use_responses_api=True,
                http_async_client=client,
                base_url="https://gateway.invalid/openai/v1",
            )
            if provider == "openai"
            else ChatFireworks(
                model="test",
                api_key=SecretStr("test"),
                async_client=AsyncFireworks(
                    api_key="test", http_client=client, base_url="https://gateway.invalid/fireworks"
                ).chat.completions,
            )
        )
        original = dict(model.model_kwargs)

        async def invoke(request: ModelRequest) -> ModelResponse:
            result = await request.model.ainvoke(request.messages, **request.model_settings)
            return ModelResponse(result=[result])

        for _ in range(2):
            await asyncio.gather(
                *(_affinity(_request(model, thread), invoke) for thread in ("thread-a", "thread-b"))
            )
        await _affinity(_request(model, None), invoke)
        await _affinity(
            _request(model, "thread-a").override(model_settings={"prompt_cache_key": "explicit"}),
            invoke,
        )
        if provider == "fireworks":
            await _affinity(
                _request(model, "thread-a").override(
                    model_settings={"extra_headers": {"X-Session-Affinity": "explicit-header"}}
                ),
                invoke,
            )

    keys = [json.loads(request.content).get("prompt_cache_key") for request in sent]
    assert keys[5] == "explicit"
    for request, key in zip(sent[:5], keys[:5], strict=True):
        body = json.loads(request.content)
        thread = (
            body["input"][0]["content"] if provider == "openai" else body["messages"][0]["content"]
        )
        assert key == (None if thread == "hello" else hashlib.sha256(thread.encode()).hexdigest())
    if provider == "fireworks":
        assert [r.headers.get("x-session-affinity") for r in sent[:5]] == keys[:5]
        assert "x-session-affinity" not in sent[5].headers
        assert keys[6] is None
        assert sent[6].headers["x-session-affinity"] == "explicit-header"
    else:
        assert all("x-session-affinity" not in r.headers for r in sent)
        assert all(r.url.path == "/openai/v1/responses" for r in sent)
    assert model.model_kwargs == original


@pytest.mark.parametrize("source", ["model", "request", "model_extra", "request_extra"])
async def test_openai_explicit_cache_keys_are_not_overwritten(source: str) -> None:
    model = ChatOpenAI(
        model="test",
        api_key=SecretStr("test"),
        model_kwargs={"prompt_cache_key": "explicit"} if source == "model" else {},
        extra_body={"prompt_cache_key": "explicit"} if source == "model_extra" else None,
    )
    request = _request(model, "thread")
    settings = (
        {"prompt_cache_key": "explicit"}
        if source == "request"
        else {"extra_body": {"prompt_cache_key": "explicit"}}
        if source == "request_extra"
        else {}
    )
    request = request.override(model_settings=settings)
    forwarded: list[ModelRequest] = []

    async def handler(selected: ModelRequest) -> ModelResponse:
        forwarded.append(selected)
        return ModelResponse(result=[AIMessage("ok")])

    await _affinity(request, handler)
    assert forwarded == [request]
    assert forwarded[0].model_settings == settings


@pytest.mark.parametrize("primary", ["openai", "fireworks"])
async def test_fallback_gets_only_its_own_affinity(
    monkeypatch: pytest.MonkeyPatch, primary: str
) -> None:
    models: dict[str, BaseChatModel] = {
        "openai": ChatOpenAI(model="test", api_key=SecretStr("test")),
        "fireworks": ChatFireworks(model="test", api_key=SecretStr("test")),
    }
    secondary = "fireworks" if primary == "openai" else "openai"
    monkeypatch.setattr(
        "rt.middleware.build_model", lambda spec, *, use_gateway: models[spec.model_id]
    )
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    run = Run(RunContext.model_validate({"__catalog": Catalog().model_dump(mode="json")}))
    request = _request(models[primary], "thread")
    planned = ModelCall(
        system_prompt="prompt",
        model=ModelSpec(model_id=primary, kwargs={}),
        fallback=ModelSpec(model_id=secondary, kwargs={}),
        use_gateway=False,
    )
    request = request.override(
        state=cast(
            AgentState, {"messages": request.messages, "open_swe_call": planned.model_dump()}
        )
    )
    seen: list[ModelRequest] = []

    async def handler(selected: ModelRequest) -> ModelResponse:
        seen.append(selected)
        if len(seen) == 1:
            raise RuntimeError("overloaded")
        return ModelResponse(result=[AIMessage("ok")])

    async def inner(selected: ModelRequest) -> ModelResponse:
        return await _affinity(selected, handler)

    await BackendRunMiddleware(run.backend).awrap_model_call(request, inner)
    for selected in seen:
        assert "x-session-affinity" not in selected.model_settings.get("extra_headers", {})
        assert ("prompt_cache_key" in selected.model_settings) == (
            type(selected.model) is ChatOpenAI
        )
    assert request.model_settings == {}
