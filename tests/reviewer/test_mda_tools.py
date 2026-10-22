import sys
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import ToolMessage, message_to_dict
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langchain_core.tracers.langchain import LangChainTracer
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langsmith import Client, tracing_context
from langsmith.run_trees import RunTree

from openswe.remote_runtime.git_metadata import GitTarget, TurnGitMetadata

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.backend import (
    BackendCallError,
    BackendUnavailableError,
    OpenSweBackend,
    PhaseReply,
    ToolReply,
    ToolSpec,
)
from rt.middleware import BackendRunMiddleware
from rt.sandbox import SandboxCreate, SandboxSpec
from rt.sandbox_tools import SandboxToolsMiddleware
from rt.tools import backend_tool
from rt.tracing import TurnTrace


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


@pytest.mark.parametrize(
    ("name", "arguments", "on_backend"),
    [
        ("execute", {"command": "cd /workspace/repo && git push origin HEAD"}, True),
        ("execute", {"command": "bash -c 'gh pr create --fill'"}, True),
        ("execute", {"command": "curl -X POST https://api.github.com/repos/o/r/pulls"}, True),
        ("execute", {"command": 'g"h" pr create --fill'}, True),
        ("execute", {"command": "git pu''sh origin HEAD"}, True),
        ("execute", {"command": "git pu\\\nsh origin HEAD"}, True),
        ("read_file", {"file_path": "/skills/deploy/SKILL.md"}, True),
        ("execute", {"command": "uv run pytest -q tests/unit"}, False),
        ("read_file", {"file_path": "/workspace/repo/README.md"}, False),
    ],
)
async def test_box_tools_leave_to_the_backend_what_its_guards_and_paths_settle(
    monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict[str, str], on_backend: bool
) -> None:
    """A command the push guards may stop never runs in the box, where they cannot see it."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = OpenSweBackend("secret-token")
    reply = ToolReply(
        message=message_to_dict(ToolMessage(content="guarded", tool_call_id="call-1"))
    )
    monkeypatch.setattr(backend, "tool", AsyncMock(return_value=reply))
    monkeypatch.setattr(backend, "tool_started", AsyncMock())
    monkeypatch.setattr(backend, "tool_result", AsyncMock())
    spec = SandboxSpec(
        create=SandboxCreate(fs_capacity_bytes=1, idle_ttl_seconds=1, delete_after_stop_seconds=1),
        tools=["execute", "read_file"],
        checks=["push_guards"],
        backend_paths=["/skills/"],
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    request = ToolCallRequest(
        tool_call={"name": name, "args": arguments, "id": "call-1", "type": "tool_call"},
        tool=None,
        state={"messages": []},
        runtime=runtime,
    )
    in_box = AsyncMock(return_value=ToolMessage(content="ran", tool_call_id="call-1"))

    await SandboxToolsMiddleware(backend, spec, 300).awrap_tool_call(request, in_box)
    await backend.reports.drain()

    assert backend.tool.await_count == int(on_backend)
    assert in_box.await_count == int(not on_backend)
    # The backend records the calls it runs; the ones run in the box are reported to it.
    assert backend.tool_started.await_count == int(not on_backend)
    assert backend.tool_result.await_count == int(not on_backend)


@pytest.mark.parametrize("next_trace_available", [True, False])
async def test_turn_reconciliation_reaches_runtime_root_llm_and_nested_calls(
    monkeypatch: pytest.MonkeyPatch, next_trace_available: bool
) -> None:
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
        if name == "prepare":
            return PhaseReply(update=await git.begin())
        if name == "before_model":
            assert isinstance(state := body["state"], dict)
            assert "git_trace_work:previous" not in state
            await nested_call.ainvoke({"text": "nested"})
            return PhaseReply(
                update=git.evidence(
                    [GitTarget(repo="org/actual", branch="local")], "work", observed_at=1
                )
            )
        if name == "after_agent":
            assert isinstance(state := body["state"], dict)
            return PhaseReply(
                trace_metadata=await git.reconcile(state), update={"git_trace_baseline": None}
            )
        return PhaseReply()

    monkeypatch.setattr(backend, "phase", phase)
    client = MagicMock(spec=Client)
    updates: list[tuple[object, dict[str, object]]] = []

    def update(run_id: object, **kwargs: object) -> None:
        updates.append((run_id, deepcopy(kwargs)))

    client.update_run.side_effect = update
    tracer = LangChainTracer(client=client, project_name="test")
    parent = RunTree(name="external request", run_type="chain", ls_client=client)
    monkeypatch.setattr(FakeListChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    graph = create_deep_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend, "turn", trace_metadata=actor)],
        checkpointer=InMemorySaver(),
    )
    config = {
        "configurable": {"thread_id": "trace-test"},
        "callbacks": [tracer],
        "metadata": {"invocation_id": "turn", "existing_key": "preserved"},
    }
    with tracing_context(enabled=True, parent=parent):
        await graph.ainvoke(
            {
                "messages": [("user", "hello")],
                "open_swe_state": {
                    "kept": True,
                    "git_trace_work:previous": {"invocation": "previous", "targets": []},
                },
            },
            config,
        )
    saved = await graph.aget_state(config)
    assert saved.values["open_swe_state"] == {"kept": True, "git_trace_baseline": None}
    assert "ls_git_repo" not in parent.metadata
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
        if run["run_type"] in {"llm", "tool"} or run.get("parent_run_id") == parent.id
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
    if not next_trace_available:

        def unavailable_trace() -> None:
            raise RuntimeError("trace lookup unavailable")

        monkeypatch.setattr(TurnTrace, "current_root", staticmethod(unavailable_trace))
    with tracing_context(enabled=True, parent=parent):
        await graph.ainvoke(
            {"messages": [("user", "next turn")]},
            {**config, "metadata": {"invocation_id": "turn"}},
        )
    assert all(run_id not in created for run_id, _ in updates[previous_update_count:])


async def test_backfill_preserves_completed_timestamps_and_does_not_finish_active_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patches: list[dict[str, object]] = []

    def capture(client: Client, data: dict[str, object], **kwargs: object) -> None:
        patches.append(deepcopy(data))

    monkeypatch.setattr(Client, "_update_run", capture)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    ended = started + timedelta(seconds=1)
    with closing(
        Client(api_key="test", api_url="https://trace.invalid", auto_batch_tracing=False)
    ) as client:
        root = RunTree(
            name="turn",
            run_type="chain",
            start_time=started,
            ls_client=client,
            extra={"metadata": {"invocation_id": "turn"}},
        )
        child = root.create_child(
            name="model",
            run_type="llm",
            start_time=started,
            extra={"metadata": {"invocation_id": "turn"}},
        )
        child.end(outputs={"answer": "done"}, end_time=ended)
        trace = TurnTrace()
        trace.root = root
        await trace.reconcile({"ls_git_repo": "org/repo", "ls_git_branch": "feature"})
    child_patch = next(patch for patch in patches if patch["id"] == child.id)
    assert child_patch["end_time"] == ended.isoformat()
    assert [patch["id"] for patch in patches] == [child.id]
    assert "outputs" not in child_patch
    assert root.end_time is None
    assert root.metadata["ls_git_repo"] == child.metadata["ls_git_repo"] == "org/repo"


@pytest.mark.parametrize("failure", ["lookup", "evidence", "export"])
async def test_optional_trace_failures_do_not_change_successful_turn_output(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    backend = OpenSweBackend("secret-token")

    def unavailable_trace() -> None:
        raise RuntimeError("trace lookup unavailable")

    if failure == "lookup":
        monkeypatch.setattr(TurnTrace, "current_root", staticmethod(unavailable_trace))

    async def phase(name: str, body: object) -> PhaseReply:
        if name == "before_model":
            TurnTrace.record_evidence({"git_trace_pr:call": {"invocation": "turn", "targets": []}})
        if failure == "evidence" and name == "after_model":
            root = TurnTrace.current_root()
            assert root is not None
            root.metadata["open_swe_git_evidence"] = "invalid"
        return PhaseReply()

    def update(run_id: object, **kwargs: object) -> None:
        if failure == "export" and "outputs" not in kwargs:
            raise RuntimeError("metadata exporter unavailable")

    monkeypatch.setattr(backend, "phase", phase)
    client = MagicMock(spec=Client)
    client.update_run.side_effect = update
    graph = create_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend, "turn")],
    )
    with tracing_context(enabled=True):
        result = await graph.ainvoke(
            {"messages": [("user", "hello")]},
            {
                "callbacks": [LangChainTracer(client=client, project_name="test")],
                "metadata": {"invocation_id": "turn"},
            },
        )
    assert result["messages"][-1].content == "done"
