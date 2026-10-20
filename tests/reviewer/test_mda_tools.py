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


async def test_turn_reconciliation_reaches_runtime_root_llm_and_nested_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copy import deepcopy

    from deepagents import create_deep_agent
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import tool
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith import Client, tracing_context
    from rt.tracing import TurnTrace

    from openswe.remote_runtime.git_metadata import GitTarget, TurnGitMetadata

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    actor = {"ls_repo_provider": "github", "ls_attribution_identifier": "trigger"}
    final = {**actor, "ls_git_repo": "org/actual", "ls_git_branch": "Exact/PR-head"}
    backend = OpenSweBackend("secret-token")
    git = TurnGitMetadata({"configurable": {"invocation_id": "turn"}, "metadata": actor})

    @tool
    async def nested_call(text: str) -> str:
        """Call a nested runnable."""
        TurnTrace.record_evidence(
            {
                **git.evidence(
                    [GitTarget(repo="org/actual", branch="local")], "work", observed_at=1
                ),
                **git.evidence(
                    [GitTarget(repo="org/later", branch="later/head")],
                    "pr-later",
                    pr=True,
                    observed_at=2,
                ),
                **git.evidence(
                    [GitTarget(repo="org/actual", branch=final["ls_git_branch"])],
                    "pr-first",
                    pr=True,
                    observed_at=3,
                ),
            }
        )
        return await RunnableLambda(lambda value: value).ainvoke(text)

    async def phase(name: str, body: dict[str, object]) -> PhaseReply:
        if name == "before_model":
            await nested_call.ainvoke({"text": "nested"})
        if name == "after_agent":
            assert isinstance(state := body["state"], dict)
            return PhaseReply(trace_metadata=await git.reconcile(state))
        return PhaseReply()

    monkeypatch.setattr(backend, "phase", phase)
    client = MagicMock(spec=Client)
    updates: list[tuple[object, dict[str, object]]] = []

    def update(run_id: object, **kwargs: object) -> None:
        updates.append((run_id, deepcopy(kwargs)))

    client.update_run.side_effect = update
    tracer = LangChainTracer(client=client, project_name="test")
    monkeypatch.setattr(FakeListChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    graph = create_deep_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend, "turn", trace_metadata=actor)],
    )
    with tracing_context(enabled=True):
        await graph.ainvoke(
            {"messages": [("user", "hello")]},
            {"callbacks": [tracer], "metadata": {"existing_key": "preserved"}},
        )
    created = {call.kwargs["id"]: call.kwargs for call in client.create_run.call_args_list}
    assert any(run["run_type"] == "llm" for run in created.values())
    assert any(run["run_type"] == "tool" for run in created.values())
    reconciled = {
        run_id: patch
        for run_id, patch in updates
        if patch.get("extra", {}).get("metadata", {}).get("ls_git_repo") == "org/actual"
    }
    attributed = {
        run_id
        for run_id, run in created.items()
        if run["run_type"] in {"llm", "tool"} or run.get("parent_run_id") is None
    }
    assert attributed <= reconciled.keys()
    for run_id in attributed:
        metadata = reconciled[run_id]["extra"]["metadata"]
        assert metadata.items() >= final.items()
        assert metadata["open_swe_git_targets"] == [
            {"repo": "org/actual", "branch": "Exact/PR-head"},
            {"repo": "org/later", "branch": "later/head"},
        ]
        assert metadata["existing_key"] == "preserved"
    assert len({run["trace_id"] for run in created.values()}) == 1
    previous_update_count = len(updates)
    final["ls_git_branch"] = "Next/turn"
    with tracing_context(enabled=True):
        await graph.ainvoke({"messages": [("user", "next turn")]}, {"callbacks": [tracer]})
    assert all(run_id not in created for run_id, _ in updates[previous_update_count:])
