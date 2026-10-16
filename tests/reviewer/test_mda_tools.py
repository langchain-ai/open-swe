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
from langgraph.types import Command

from openswe import completion

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.backend import BackendCallError, BackendUnavailableError, OpenSweBackend, ToolSpec
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
async def test_phase_recovers_from_gateway_failures(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    hook: Literal["prepare", "before_model", "after_model", "after_agent"],
    status: int,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    sleep = AsyncMock()
    monkeypatch.setattr("rt.backend.asyncio.sleep", sleep)
    request = httpx2.Request("POST", "https://backend.invalid")
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(
        side_effect=[httpx2.Response(status, request=request)] * 3
        + [httpx2.Response(200, request=request, json={"update": {"recovered": True}})]
    )
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    reply = await OpenSweBackend("secret-token").phase(hook, {})

    assert reply.update == {"recovered": True}
    assert client.post.await_count == 4
    assert [call.args[0] for call in sleep.await_args_list] == [1, 3, 9]
    assert len([record for record in caplog.records if "retrying" in record.message]) == 3


@pytest.mark.parametrize(
    ("hook", "status", "failures", "source"),
    [
        ("before_model", 503, 1, "slack"),
        ("before_model", 503, 4, "slack"),
        ("after_model", 503, 4, "slack"),
        ("before_model", 500, 1, "slack"),
        ("after_model", 500, 1, "slack"),
        ("before_model", 503, 4, "github"),
    ],
)
async def test_model_phase_failure_reaches_completion(
    monkeypatch: pytest.MonkeyPatch, hook: str, status: int, failures: int, source: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.backend.asyncio.sleep", AsyncMock())
    attempts = 0

    async def post(url: str, **kwargs: object) -> httpx2.Response:
        nonlocal attempts
        request = httpx2.Request("POST", url)
        if url.endswith(f"/{hook}"):
            attempts += 1
            if attempts <= failures:
                return httpx2.Response(status, request=request)
        return httpx2.Response(200, request=request, json={})

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    graph = create_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(OpenSweBackend("secret-token"), "invocation-1")],
    )
    if status == 503 and failures == 1:
        result = await graph.ainvoke({"messages": [("user", "hello")]})
        assert result["messages"][-1].content == "done"
        assert attempts == 2
        return

    error_type = BackendUnavailableError if status == 503 else BackendCallError
    with pytest.raises(error_type) as failure:
        await graph.ainvoke({"messages": [("user", "hello")]})
    assert attempts == failures

    threads = MagicMock()
    metadata = (
        {
            "source": "slack",
            "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "123.45"}},
        }
        if source == "slack"
        else {
            "source": "github",
            "repo": {"owner": "acme", "name": "widgets"},
            "source_context": {"pr_number": 7},
        }
    )
    threads.get = AsyncMock(return_value={"metadata": metadata})
    threads.update = AsyncMock()
    platform = MagicMock(threads=threads)
    monkeypatch.setattr(completion, "langgraph_client", lambda: platform)
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(completion, "post_slack_thread_reply", reply)
    monkeypatch.setattr(completion, "post_github_comment", reply)
    monkeypatch.setattr(
        completion, "get_github_app_installation_token", AsyncMock(return_value="token")
    )

    result = await completion.handle_run_completion(
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "status": "error",
            "error": {"error": type(failure.value).__name__, "message": "An error occurred"},
        }
    )

    assert result == {"status": "ok", "reason": "failure reply posted"}
    assert reply.await_args is not None
    assert reply.await_args.args[:2] == (
        ("C1", "123.45") if source == "slack" else ({"owner": "acme", "name": "widgets"}, 7)
    )
    assert "Open SWE backend was interrupted" in reply.await_args.args[2]
    assert "Re-trigger" in reply.await_args.args[2]
