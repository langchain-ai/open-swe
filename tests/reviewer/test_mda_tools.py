import sys
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import ToolMessage, message_to_dict
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.backend import (
    BackendCallError,
    BackendUnavailableError,
    OpenSweBackend,
    PhaseReply,
    ToolSpec,
)
from rt.middleware import BackendRunMiddleware
from rt.tools import backend_tool


@pytest.mark.parametrize(
    ("hook", "failure", "failures", "attempts"),
    [
        ("call", "connect", 2, 3),
        ("call", "connect", 3, 3),
        ("before_model", "connect", 2, 3),
        ("before_model", "connect", 3, 3),
        ("call", "gateway", 1, 1),
    ],
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
        if failures == 3:
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


@pytest.mark.parametrize(
    ("hook", "status"),
    [
        ("prepare", 503),
        ("before_model", 502),
        ("before_model", 503),
        ("before_model", 504),
        ("after_model", 503),
        ("after_agent", 503),
    ],
)
async def test_phase_gateway_failure_is_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
    hook: Literal["prepare", "before_model", "after_model", "after_agent"],
    status: int,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    sleep = AsyncMock()
    monkeypatch.setattr("rt.backend.asyncio.sleep", sleep)
    request = httpx2.Request("POST", "https://backend.invalid")
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(
        side_effect=[httpx2.Response(status, request=request)]
        + [httpx2.Response(200, request=request, json={"update": {"recovered": True}})]
    )
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    with pytest.raises(BackendUnavailableError):
        await OpenSweBackend("secret-token").phase(hook, {})

    assert client.post.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize("hook", ["prepare", "before_model", "after_model"])
async def test_phase_failure_ends_with_a_run_scoped_marker(
    monkeypatch: pytest.MonkeyPatch, hook: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    backend = OpenSweBackend("secret-token")

    async def phase(name: str, body: object) -> PhaseReply:
        if name == hook:
            raise BackendUnavailableError(f"{hook} failed")
        assert name != "after_agent"
        return PhaseReply()

    monkeypatch.setattr(backend, "phase", phase)
    graph = create_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend, "invocation-1")],
    )

    values = await graph.ainvoke({"messages": [("user", "hello")]})

    assert values["open_swe_backend_failure"] == {"run_id": "run-1", "phase": hook}
    assert values["messages"][-1].content == ("done" if hook == "after_model" else "hello")


async def test_a_new_run_clears_a_checkpointed_backend_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    current_run = "run-1"
    monkeypatch.setattr("rt.middleware.run_id", lambda: current_run)
    backend = OpenSweBackend("secret-token")
    monkeypatch.setattr(
        backend,
        "phase",
        AsyncMock(side_effect=[PhaseReply(), BackendUnavailableError("before_model failed")]),
    )
    graph = create_agent(
        model=FakeListChatModel(responses=["recovered"]),
        middleware=[BackendRunMiddleware(backend, "invocation-1")],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "thread-1"}}
    result = await graph.ainvoke({"messages": [("user", "hello")]}, config)
    assert result["open_swe_backend_failure"]["run_id"] == "run-1"

    current_run = "run-2"
    monkeypatch.setattr(backend, "phase", AsyncMock(return_value=PhaseReply()))
    result = await graph.ainvoke({"messages": [("user", "retry")]}, config)

    assert result["open_swe_backend_failure"] is None
    assert result["messages"][-1].content == "recovered"


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
