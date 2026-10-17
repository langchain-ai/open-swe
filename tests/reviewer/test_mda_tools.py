import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage, message_to_dict
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.backend import BackendCallError, BackendUnavailableError, OpenSweBackend, ToolSpec
from rt.tools import backend_tool


@pytest.mark.parametrize("hook", ["call", "before_model"])
@pytest.mark.parametrize(
    ("failure", "failures", "attempts"),
    [("connect", 2, 3), ("connect", 3, 3), ("gateway", 1, 1)],
)
async def test_backend_connection_failures(
    monkeypatch: pytest.MonkeyPatch, hook: str, failure: str, failures: int, attempts: int
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.backend.asyncio.sleep", AsyncMock())
    request = httpx2.Request("POST", "https://backend.invalid")
    message = message_to_dict(ToolMessage(content="done", tool_call_id="call-1"))
    response = httpx2.Response(200, request=request, json={"message": message})
    failure_result = (
        httpx2.ConnectError("secret-token")
        if failure == "connect"
        else httpx2.Response(503, request=request)
    )
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=[failure_result] * failures + [response])
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    backend = OpenSweBackend("secret-token")
    if hook == "before_model":
        if failures == 3 or failure == "gateway":
            with pytest.raises(BackendUnavailableError):
                await backend.phase("before_model", {})
        else:
            await backend.phase("before_model", {})
    else:
        served = backend_tool(
            ToolSpec(name="execute", description="Execute", parameters={"type": "object"}), backend
        )
        runtime = ToolRuntime(
            state={"messages": []},
            context=None,
            config={},
            stream_writer=lambda event: None,
            tool_call_id="call-1",
            store=None,
        )
        result = await served.coroutine(runtime=runtime)
        assert isinstance(result, Command)
        [result_message] = result.update["messages"]
        assert result_message.tool_call_id == "call-1"
        assert result_message.status == ("success" if failures == 2 else "error")
        assert "secret-token" not in result_message.content
    assert client.post.await_count == attempts


async def test_a_failing_backend_hook_fails_the_run_with_what_it_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    request = httpx2.Request("POST", "https://backend.invalid")
    failure = {"error": "RuntimeError", "message": "no sandbox provider"}
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(return_value=httpx2.Response(500, request=request, json=failure))
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    with pytest.raises(BackendCallError, match="prepare failed: RuntimeError: no sandbox provider"):
        await OpenSweBackend("secret-token").phase("prepare", {})


@pytest.mark.parametrize("failure_on_fallback", [False, True])
@pytest.mark.parametrize("status", [402, 429])
async def test_gateway_policy_failure_reaches_completion(
    monkeypatch: pytest.MonkeyPatch, failure_on_fallback: bool, status: int
) -> None:
    import openai
    from langchain.agents.middleware import ModelRetryMiddleware
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import AIMessage
    from rt.middleware import BackendRunMiddleware, ModelCall
    from rt.models import ModelSpec

    from openswe import completion
    from openswe.middleware import model_errors
    from openswe.remote_runtime.hosted import hosted_graphs
    from openswe.remote_runtime.tokens import RemoteRun
    from openswe.utils.errors import LAST_MODEL_ERROR_KEY

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "root-run")
    primary = FakeListChatModel(responses=["primary"])
    fallback = FakeListChatModel(responses=["fallback"])
    monkeypatch.setattr("rt.middleware.build_model", lambda *args, **kwargs: fallback)
    recorded: dict[str, object] = {}

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        assert thread_id == "thread-1"
        recorded.update(metadata)

    client = MagicMock()
    client.threads.update = update
    monkeypatch.setattr(model_errors, "get_client", lambda: client)
    backend = OpenSweBackend("token")
    graph = hosted_graphs()["agent"]
    remote = RemoteRun(thread_id="thread-1", assistant_id="agent", configurable={})

    async def report(hook: str, body: dict[str, object]) -> object:
        return await graph.hook(remote, hook, body)

    monkeypatch.setattr(backend, "call", report)
    middleware = BackendRunMiddleware(backend, "invocation")
    retry = ModelRetryMiddleware(retry_on=(TimeoutError,))
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://gateway.invalid"))
    error = openai.APIStatusError(
        "Payment required" if status == 402 else "Blocked by gateway policies",
        response=response,
        body={},
    )
    attempts = 0

    async def model(request: ModelRequest) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        if failure_on_fallback and request.model is primary:
            raise openai.APIStatusError(
                "unavailable", response=httpx2.Response(503, request=response.request), body={}
            )
        raise error

    async def with_retry(request: ModelRequest) -> ModelResponse:
        return await retry.awrap_model_call(request, model)

    request = ModelRequest(
        model=primary,
        messages=[],
        state={
            "messages": [],
            "open_swe_call": ModelCall(
                system_prompt="system",
                model=None,
                fallback=ModelSpec(model_id="openai:fallback", kwargs={}),
                use_gateway=True,
            ).model_dump(),
        },
    )
    with pytest.raises(openai.APIStatusError) as caught:
        await middleware.awrap_model_call(request, with_retry)
    assert caught.value is error
    assert attempts == (2 if failure_on_fallback else 1)
    assert recorded[LAST_MODEL_ERROR_KEY] == {
        "run_id": "root-run",
        "error_type": "APIStatusError",
        "code": "gateway_policy_blocked",
    }
    code = completion._failure_reason_code({"error": "APIStatusError"}, recorded, "root-run")
    assert code == "gateway_policy_blocked"
    assert (
        completion._failure_reason_code({"error": "APIStatusError"}, recorded, "other-run")
        == "provider_unavailable"
    )
    text = completion._failure_text("error", reason_code=code)
    assert "workspace admin" in text
    assert "Resending will not help" in text
    assert "Send another message" not in text

    async def transient(request: ModelRequest) -> ModelResponse:
        if request.model is primary:
            raise openai.APIStatusError(
                "unavailable", response=httpx2.Response(503, request=response.request), body={}
            )
        return ModelResponse(result=[AIMessage(content="recovered")])

    result = await middleware.awrap_model_call(request, transient)
    assert result.result[0].content == "recovered"
