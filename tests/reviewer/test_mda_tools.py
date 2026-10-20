import sys
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    GenericFakeChatModel,
)
from langchain_core.messages import AIMessage, ToolMessage, message_to_dict
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
    ("failure", "failures", "attempts"),
    [("connect", 10, 11), ("connect", 11, 11), (503, 1, 1), (504, 1, 1)],
)
async def test_a_tool_call_the_backend_never_answered(
    monkeypatch: pytest.MonkeyPatch, failure: str | int, failures: int, attempts: int
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
        else httpx2.Response(failure, request=request)
    )
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=[failure_result] * failures + [response])
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    served = backend_tool(
        ToolSpec(name="execute", description="Execute", parameters={"type": "object"}),
        OpenSweBackend("secret-token"),
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
    assert result_message.status == ("success" if failures == 10 else "error")
    assert "secret-token" not in result_message.content
    assert client.post.await_count == attempts


@pytest.mark.parametrize(
    ("hook", "status"),
    [
        ("prepare", 504),
        ("before_model", 502),
        ("before_model", 504),
        ("after_model", 503),
        ("after_agent", 504),
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


@pytest.mark.parametrize(("failures", "recovers"), [(10, True), (11, False)])
@pytest.mark.parametrize("hook", ["prepare", "before_model"])
async def test_a_run_waits_out_a_backend_rollout(
    monkeypatch: pytest.MonkeyPatch,
    hook: Literal["prepare", "before_model"],
    failures: int,
    recovers: bool,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    sleep = AsyncMock()
    monkeypatch.setattr("rt.backend.asyncio.sleep", sleep)
    request = httpx2.Request("POST", "https://backend.invalid")
    # Only a call the backend could not have run is resent; prepare is also resent after a 502.
    unavailable = [httpx2.Response(503, request=request), httpx2.ConnectError("secret-token")]
    if hook == "prepare":
        unavailable.append(httpx2.Response(502, request=request))
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(
        side_effect=[unavailable[i % len(unavailable)] for i in range(failures)]
        + [httpx2.Response(200, request=request, json={"update": {"prepared": True}})]
    )
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    backend = OpenSweBackend("secret-token")

    if recovers:
        reply = await backend.phase(hook, {})
        assert reply.update == {"prepared": True}
        assert sum(call.args[0] for call in sleep.await_args_list) >= 120
    else:
        with pytest.raises(BackendUnavailableError):
            await backend.phase(hook, {})
    assert client.post.await_count == min(failures + 1, 11)


@pytest.mark.parametrize("hook", ["prepare", "before_model", "after_model"])
async def test_an_unreachable_backend_fails_the_run(
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

    with pytest.raises(BackendUnavailableError):
        await graph.ainvoke({"messages": [("user", "hello")]})


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


class _ToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools: object, **kwargs: object) -> _ToolCallingModel:
        return self


async def test_a_launched_worker_carries_a_grant_never_its_coordinators_run_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deepagents.middleware import async_subagents
    from rt.middleware import AsyncWorkerLaunchMiddleware

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    backend = OpenSweBackend("coordinator-token")
    calls: list[tuple[str, dict[str, object]]] = []

    async def call(hook: str, body: dict[str, object], **kwargs: object) -> dict[str, object]:
        calls.append((hook, dict(body)))
        if hook == "launch_worker":
            return {"grant": "worker-grant", "assignment": "Your assignment: Fix login"}
        return {"worker": {"worker_thread_id": body["worker_thread_id"], "task_id": "task-1"}}

    monkeypatch.setattr(backend, "call", call)
    launches = async_subagents.AsyncSubAgentMiddleware(
        async_subagents=[
            {
                "name": "general-purpose",
                "description": "A task worker.",
                "graph_id": "open-swe--general-purpose",
            }
        ]
    )
    client = MagicMock()
    client.threads.create = AsyncMock(return_value={"thread_id": "worker-thread"})
    client.runs.create = AsyncMock(return_value={"run_id": "worker-run"})
    # The launch's in-process LangGraph client, the deployment's own Agent Server.
    monkeypatch.setattr(async_subagents._ClientCache, "get_async", lambda self, name: client)
    model = _ToolCallingModel(
        messages=iter(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "start_async_task",
                            "args": {
                                "description": "Fix login",
                                "subagent_type": "general-purpose",
                            },
                            "id": "launch-1",
                        },
                        {
                            "name": "check_async_task",
                            "args": {"task_id": "worker-thread"},
                            "id": "check-1",
                        },
                        # An update would interrupt the worker; Open SWE queues messages instead.
                        {
                            "name": "update_async_task",
                            "args": {"task_id": "worker-thread", "message": "Stop"},
                            "id": "update-1",
                        },
                    ],
                ),
                AIMessage("Launched."),
            ]
        )
    )
    graph = create_agent(model, middleware=[AsyncWorkerLaunchMiddleware(backend), launches])

    values = await graph.ainvoke(
        {"messages": [("user", "fix login in the background")]},
        {
            "configurable": {"thread_id": "coordinator-thread", "assistant_id": "open-swe"},
            "metadata": {"assistant_id": "open-swe"},
        },
        context={"graph": "agent", "__run_token": "coordinator-token"},
    )

    launch = client.runs.create.await_args.kwargs
    assert launch["context"] == {"graph": "agent", "__worker_grant": "worker-grant"}
    assert launch["input"] == {
        "messages": [{"role": "user", "content": "Your assignment: Fix login"}]
    }
    assert "coordinator-token" not in repr(client.runs.create.await_args)
    # No parent assistant, so the worker never wakes the coordinator on MDA's own.
    assert client.threads.create.await_args.kwargs["metadata"] == {
        "subagent_name": "general-purpose",
        "parent_thread_id": "coordinator-thread",
    }
    assert calls == [
        ("launch_worker", {"tool_call_id": "launch-1", "instructions": "Fix login"}),
        (
            "worker_launched",
            {
                "tool_call_id": "launch-1",
                "instructions": "Fix login",
                "worker_thread_id": "worker-thread",
                "run_id": "worker-run",
            },
        ),
    ]
    # Open SWE's task messages report the worker; deepagents does not poll it too.
    assert not values.get("async_tasks")
    results = {m.tool_call_id: m for m in values["messages"] if isinstance(m, ToolMessage)}
    assert "worker-thread" in str(results["launch-1"].content)
    assert results["check-1"].status == results["update-1"].status == "error"
    client.runs.create.assert_awaited_once()


async def test_a_launched_worker_adopts_its_own_run_token_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "worker-run")
    request = httpx2.Request("POST", "https://backend.invalid")
    seen: list[tuple[str, str]] = []

    async def post(
        url: str, *, headers: dict[str, str], json: dict[str, object], **kwargs: object
    ) -> httpx2.Response:
        hook = url.rsplit("/", 1)[-1]
        seen.append((hook, headers["Authorization"]))
        if hook == "adopt":
            assert json == {
                "thread_id": "worker-thread",
                "run_id": "worker-run",
                "instructions": "Fix login",
            }
            return httpx2.Response(200, request=request, json={"run_token": "worker-token"})
        return httpx2.Response(200, request=request, json={})

    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=post)
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    backend = OpenSweBackend(None, worker_grant="worker-grant")
    graph = create_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend, None)],
    )

    await graph.ainvoke(
        {"messages": [("user", "Fix login")]}, {"configurable": {"thread_id": "worker-thread"}}
    )

    assert seen[0] == ("adopt", "Bearer worker-grant")
    assert [hook for hook, _ in seen[1:3]] == ["catalog", "prepare"]
    assert all(bearer == "Bearer worker-token" for _, bearer in seen[1:])
