import sys
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import httpx
import httpx2
import pytest
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import ToolMessage, message_to_dict
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from openswe import completion

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
        ("prepare", 503, 4, "slack"),
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

    payload: dict[str, object] = {"thread_id": "thread-1", "run_id": "run-1"}
    if status == 503:
        values = await graph.ainvoke({"messages": [("user", "hello")]})
        assert values["open_swe_backend_failure"] == {"run_id": "run-1", "phase": hook}
        assert not any(
            call.args[0].endswith("/after_agent") for call in client.post.await_args_list
        )
        assert values["messages"][-1].content == ("done" if hook == "after_model" else "hello")
        payload.update(
            status="success",
            values={
                **values,
                "messages": [message_to_dict(message) for message in values["messages"]],
            },
        )
    else:
        with pytest.raises(BackendCallError) as failure:
            await graph.ainvoke({"messages": [("user", "hello")]})
        payload.update(status="error", error={"error": type(failure.value).__name__})
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

    settle = AsyncMock()
    pickup = AsyncMock()
    telemetry = AsyncMock()
    monkeypatch.setattr(completion, "_settle_transcript_turn", settle)
    monkeypatch.setattr(completion, "_start_run_for_pending_follow_ups", pickup)
    monkeypatch.setattr(completion, "_finalize_agent_usage_telemetry", telemetry)

    from openswe.api.app import app

    monkeypatch.setattr(completion, "RUN_COMPLETE_WEBHOOK_SECRET", "completion-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as webhook:
        response = await webhook.post(
            "/webhooks/run-complete?token=completion-secret", json=payload
        )
    assert response.status_code == 200
    result = response.json()

    settle.assert_awaited_once_with("thread-1", "run-1", "error")
    assert telemetry.await_args is not None
    assert telemetry.await_args.args[1] == "error"
    pickup.assert_not_awaited()

    assert result == {"status": "ok", "reason": "failure reply posted"}
    assert reply.await_args is not None
    assert reply.await_args.args[:2] == (
        ("C1", "123.45") if source == "slack" else ({"owner": "acme", "name": "widgets"}, 7)
    )
    text = reply.await_args.args[2]
    if status == 503:
        assert "Open SWE backend was interrupted" in text
        assert "Re-trigger" in text
    else:
        assert "unexpected error" in text
        assert "backend was interrupted" not in text


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
