import asyncio
from collections.abc import Mapping, Sequence
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Literal, Self, cast
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx
import httpx2
import openai
import pytest
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    GenericFakeChatModel,
)
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolCall,
    ToolMessage,
    message_to_dict,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tools import tool
from langchain_core.tracers.langchain import LangChainTracer, wait_for_all_tracers
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from langgraph_sdk.errors import (
    APIStatusError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from langsmith import Client, agent_address, get_tracing_context, tracing_context
from langsmith.run_trees import RunTree, WriteReplica
from managed_deepagents import define_deep_agent
from managed_deepagents.runtime import compile_managed_agent_async
from pydantic import JsonValue
from rt.backend import (
    BackendCallError,
    BackendUnavailableError,
    OpenSweBackend,
    PhaseReply,
    ToolReply,
    ToolSpec,
)
from rt.middleware import BackendRunMiddleware, RunState
from rt.model_calls import model_call_guards
from rt.models import ModelSpec
from rt.sandbox import SandboxCreate, SandboxSpec
from rt.sandbox_tools import SandboxToolsMiddleware
from rt.tools import backend_tool
from rt.tracing import TurnTrace


def _run_metadata(patch: Mapping[str, object]) -> Mapping[str, object]:
    """The metadata a LangSmith run update sets."""
    extra = patch.get("extra")
    metadata = extra.get("metadata") if isinstance(extra, Mapping) else None
    return metadata if isinstance(metadata, Mapping) else {}


def _evidence(
    kind: Literal["work", "pr"], key: str, repo: str, branch: str, observed_at: int
) -> dict[str, JsonValue]:
    """Git evidence as the backend's hooks record it for the turn ``turn``."""
    return {
        f"git_trace_{kind}:{key}": {
            "invocation": "turn",
            "observed_at": observed_at,
            "targets": [{"repo": repo, "branch": branch}],
        }
    }


@pytest.mark.parametrize(
    ("replicate", "override", "projects"),
    [
        (False, "canonical", {"canonical"}),
        (True, "canonical", {"canonical", "deployment"}),
        (False, None, {"canonical", "deployment"}),
    ],
)
@pytest.mark.parametrize("has_parent", [False, True])
async def test_managed_traces_keep_the_requested_project_without_mirroring(
    monkeypatch: pytest.MonkeyPatch,
    replicate: bool,
    override: str | None,
    projects: set[str],
    has_parent: bool,
) -> None:
    monkeypatch.setattr(FakeListChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    client = MagicMock()
    example_id = "00000000-0000-4000-8000-000000000001"
    replicas: list[WriteReplica] = [
        {
            "project_name": "canonical",
            "updates": {"reference_example_id": example_id},
            "auth": {"api_key": "synthetic-replica-key"},
        },
        {"project_name": "deployment"},
    ]
    parent = RunTree(name="upstream", ls_client=client, replicas=replicas)
    parent.post()
    parent_spans = {
        call.kwargs["session_name"]: call.kwargs["id"] for call in client.create_run.call_args_list
    }
    client.reset_mock()
    agent = await compile_managed_agent_async(
        define_deep_agent(
            name="test-agent",
            model=FakeListChatModel(responses=["first", "second"]),
            **({"replicate_traces": False} if not replicate else {}),
        ),
        {"configurable": {"__langsmith_project__": override}},
        report_telemetry=False,
    )
    callbacks = RunCollectorCallbackHandler()
    with tracing_context(
        enabled=True, client=client, parent=parent if has_parent else False, replicas=replicas
    ):
        for answer in ("first", "second"):
            result = await agent.ainvoke(
                {"messages": [("user", "hello")]},
                config={"run_name": "test-agent", "callbacks": [callbacks]},
            )
            assert result["messages"][-1].content == answer
            assert get_tracing_context()["replicas"] == replicas
    wait_for_all_tracers()
    assert len([run for run in callbacks.traced_runs if run.name == "test-agent"]) == 2
    spans = [call.kwargs for call in client.create_run.call_args_list]
    assert {span["session_name"] for span in spans} == projects
    for project in projects:
        project_spans = [span for span in spans if span["session_name"] == project]
        roots = [span for span in project_spans if span["name"] == "test-agent"]
        models = [span for span in project_spans if span["run_type"] == "llm"]
        assert len(roots) == len(models) == 2
        assert len({model["id"] for model in models}) == 2
        for root in roots:
            assert root["parent_run_id"] == (parent_spans[project] if has_parent else None)
            [model] = [m for m in models if str(root["id"]) in m["dotted_order"]]
            assert model["trace_id"] == root["trace_id"]
            if project == "canonical":
                assert root["reference_example_id"] == example_id
                assert root["_replica_auths"][0].api_key == "synthetic-replica-key"
    assert parent.replicas == replicas


@pytest.mark.parametrize(
    ("failure", "failures", "attempts"),
    [("connect", 10, 11), ("connect", 11, 11), (503, 1, 1), (504, 1, 1)],
)
async def test_a_tool_call_the_backend_never_answered(
    monkeypatch: pytest.MonkeyPatch, failure: Literal["connect"] | int, failures: int, attempts: int
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
    assert served.coroutine is not None
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
@pytest.mark.parametrize("hook", ["prepare", "launch_worker", "before_model"])
async def test_a_run_waits_out_a_backend_rollout(
    monkeypatch: pytest.MonkeyPatch,
    hook: Literal["prepare", "launch_worker", "before_model"],
    failures: int,
    recovers: bool,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    sleep = AsyncMock()
    monkeypatch.setattr("rt.backend.asyncio.sleep", sleep)
    request = httpx2.Request("POST", "https://backend.invalid")
    # Only a call the backend could not have run is resent, or one that is safe to repeat.
    unavailable = [httpx2.Response(503, request=request), httpx2.ConnectError("secret-token")]
    if hook != "before_model":
        unavailable.append(httpx2.Response(502, request=request))
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(
        side_effect=[unavailable[i % len(unavailable)] for i in range(failures)]
        + [httpx2.Response(200, request=request, json={"update": {"prepared": True}})]
    )
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    backend = OpenSweBackend("secret-token")

    if recovers:
        reply = await backend.call(hook, {})
        assert reply == {"update": {"prepared": True}}
        assert sum(call.args[0] for call in sleep.await_args_list) >= 120
    else:
        with pytest.raises(BackendUnavailableError):
            await backend.call(hook, {})
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


class _RecordingAnthropic(ChatAnthropic):
    sent: list[BaseMessage] = []

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self.sent.extend(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


async def test_the_model_the_backend_names_gets_a_replayable_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    named = _RecordingAnthropic(model_name="claude-opus-5-5", api_key="test-key", sent=[])
    monkeypatch.setattr("rt.middleware.build_model", lambda spec, use_gateway: named)
    backend = OpenSweBackend("secret-token")

    async def phase(name: str, body: object) -> PhaseReply:
        if name != "before_model":
            return PhaseReply()
        return PhaseReply(
            system_prompt="You are Open SWE.",
            model=ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={}),
        )

    monkeypatch.setattr(backend, "phase", phase)
    # As the agent orders them: the guards inside the middleware that names the model.
    middleware = cast(
        list[AgentMiddleware],
        [BackendRunMiddleware(backend, "invocation-1"), *model_call_guards()],
    )
    graph = create_agent(model=FakeListChatModel(responses=["unused"]), middleware=middleware)
    reply = AIMessage(
        content=[
            {"type": "thinking", "signature": "sig", "thinking": ""},
            {"type": "text", "text": "hello"},
        ]
    )

    await graph.ainvoke({"messages": [HumanMessage("hi"), reply, HumanMessage("again")]})

    assert [m.content for m in named.sent if isinstance(m, AIMessage)] == [
        [{"type": "text", "text": "hello"}]
    ]


class _StalledAnthropic(ChatAnthropic):
    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        await asyncio.sleep(60)
        raise AssertionError("cancelled by the deadline")


async def test_a_stalled_model_call_goes_to_the_fallback_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    fallback = _RecordingAnthropic(model_name="claude-sonnet-5-5", api_key="test-key", sent=[])
    built = {
        "anthropic:claude-opus-5-5": _StalledAnthropic(model_name="claude-opus-5-5", api_key="k"),
        "anthropic:claude-sonnet-5-5": fallback,
    }
    monkeypatch.setattr("rt.middleware.build_model", lambda spec, use_gateway: built[spec.model_id])
    backend = OpenSweBackend("secret-token")

    async def phase(name: str, body: object) -> PhaseReply:
        if name != "before_model":
            return PhaseReply()
        return PhaseReply(
            system_prompt="You are Open SWE.",
            model=ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={}),
            fallback=ModelSpec(model_id="anthropic:claude-sonnet-5-5", kwargs={}),
        )

    monkeypatch.setattr(backend, "phase", phase)
    middleware = cast(
        list[AgentMiddleware],
        [
            BackendRunMiddleware(backend, "invocation-1"),
            *model_call_guards(timeout_seconds=0.05),
        ],
    )
    graph = create_agent(model=FakeListChatModel(responses=["unused"]), middleware=middleware)

    result = await graph.ainvoke({"messages": [HumanMessage("hi")]})

    assert result["messages"][-1].content == "done"
    assert fallback.sent


async def test_a_stalled_subagent_call_answers_with_the_timeout() -> None:
    # Its `task` call hands this answer back; a raise would end the delegating run.
    subagent = create_agent(
        model=_StalledAnthropic(model_name="claude-opus-5-5", api_key="test-key"),
        middleware=model_call_guards(timeout_seconds=0.05, subagent=True),
    )

    result = await subagent.ainvoke({"messages": [HumanMessage("look into it")]})

    answer = result["messages"][-1]
    assert isinstance(answer, AIMessage)
    assert "timed out" in answer.text
    assert "ModelCallTimeoutError" in answer.text


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
    on_backend_call = AsyncMock(return_value=reply)
    started = AsyncMock()
    finished = AsyncMock()
    monkeypatch.setattr(backend, "tool", on_backend_call)
    monkeypatch.setattr(backend, "tool_started", started)
    monkeypatch.setattr(backend, "tool_result", finished)
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

    await SandboxToolsMiddleware(backend, spec, 300, check_reads=True).awrap_tool_call(
        request, in_box
    )
    await backend.reports.drain()

    assert on_backend_call.await_count == int(on_backend)
    assert in_box.await_count == int(not on_backend)
    # The backend records the calls it runs; the ones run in the box are reported to it.
    assert started.await_count == int(not on_backend)
    assert finished.await_count == int(not on_backend)


@pytest.mark.parametrize("next_trace_available", [True, False])
async def test_turn_reconciliation_reaches_runtime_root_llm_and_nested_calls(
    monkeypatch: pytest.MonkeyPatch, next_trace_available: bool
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    actor = {"ls_repo_provider": "github", "ls_attribution_identifier": "trigger"}
    final = {**actor, "ls_git_repo": "org/actual", "ls_git_branch": "Exact/PR-head"}
    backend = OpenSweBackend("secret-token")

    @tool
    async def nested_call(text: str) -> str:
        """Call a nested runnable."""
        TurnTrace.record_evidence(
            {
                **_evidence("work", "work", "org/actual", "local", 1),
                **_evidence("pr", "pr-later", "org/later", "later/head", 2),
                **_evidence("pr", "pr-first", "org/actual", final["ls_git_branch"], 3),
            }
        )
        return await RunnableLambda(lambda value: value).ainvoke(text)

    async def phase(name: str, body: dict[str, object]) -> PhaseReply:
        if name == "prepare":
            return PhaseReply(
                update={"git_trace_baseline": {"invocation": "turn", "snapshot": None}}
            )
        if name == "before_model":
            assert isinstance(state := body["state"], dict)
            assert "git_trace_work:previous" not in state
            await nested_call.ainvoke({"text": "nested"})
            return PhaseReply(update=_evidence("work", "work", "org/actual", "local", 1))
        if name == "after_agent":
            # What the backend reconciles from that evidence: the earliest PR target first.
            return PhaseReply(
                trace_metadata={
                    **final,
                    "open_swe_git_targets": [
                        {"repo": "org/actual", "branch": final["ls_git_branch"]},
                        {"repo": "org/later", "branch": "later/head"},
                    ],
                },
                update={"git_trace_baseline": None},
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
    config: RunnableConfig = {
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
        run_id: metadata
        for run_id, patch in updates
        if (metadata := _run_metadata(patch)).get("ls_git_repo") == "org/actual"
    }
    attributed = {
        run_id
        for run_id, run in created.items()
        if run["run_type"] in {"llm", "tool"} or run.get("parent_run_id") == parent.id
    }
    assert attributed <= reconciled.keys()
    for run_id in attributed:
        metadata = reconciled[run_id]
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


@pytest.mark.parametrize("destination", ["project", "address"])
async def test_backfill_preserves_destination_timing_and_outputs(
    monkeypatch: pytest.MonkeyPatch, destination: str
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
            project_name="source" if destination != "address" else None,
            address=agent_address("open-swe", "production") if destination == "address" else None,
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
    for patch in patches:
        assert patch["end_time"] == ended.isoformat()
        assert "inputs" not in patch
        assert patch["outputs"] == {"answer": "done"}
        assert isinstance(extra := patch["extra"], dict)
        assert extra["metadata"]["ls_git_repo"] == "org/repo"
        assert extra["metadata"]["ls_git_branch"] == "feature"
    assert len(patches) == 1
    if destination == "project":
        assert child_patch["session_name"] == "source"
    else:
        assert child_patch["address"] == "lrn:agents/open-swe/environments/production"
    assert child.outputs == {"answer": "done"}
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
        return PhaseReply(trace_metadata={"ls_git_repo": "org/actual"})

    export_failed = False

    def update(run_id: object, **kwargs: object) -> None:
        nonlocal export_failed
        extra = kwargs.get("extra")
        if (
            failure == "export"
            and not export_failed
            and isinstance(extra, dict)
            and extra.get("metadata", {}).get("ls_git_repo") == "org/actual"
        ):
            export_failed = True
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
    assert export_failed == (failure == "export")


async def test_box_call_git_evidence_reaches_the_turn_after_its_run_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backend answers a box call's report after the call's run closed; its evidence still counts."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    evidence = _evidence("work", "call-1", "org/actual", "work", 1)
    turn = RunTree(name="turn", run_type="chain", extra={"metadata": {"open_swe_git_evidence": {}}})
    roots: list[RunTree | None] = [turn]
    monkeypatch.setattr(TurnTrace, "current_root", staticmethod(lambda: roots[0]))
    backend = OpenSweBackend("secret-token")
    monkeypatch.setattr(backend, "tool_result", AsyncMock(return_value=evidence))

    backend.reports.finished({}, ToolMessage(content="ran", tool_call_id="call-1"))
    roots[0] = None  # The call's run has closed: the turn is no longer found from here.
    await backend.reports.drain()

    assert turn.metadata["open_swe_git_evidence"] == evidence


async def test_a_read_file_the_backend_serves_gets_the_read_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /blobs/ file with an image's extension but not its bytes never reaches the model as one."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    backend = OpenSweBackend("secret-token")
    fake = ToolMessage(
        content=[
            {"type": "image", "base64": "eyJlcnJvciI6ICJleHBpcmVkIn0=", "mime_type": "image/png"}
        ],
        tool_call_id="call-1",
        name="read_file",
    )
    monkeypatch.setattr(
        backend, "tool", AsyncMock(return_value=ToolReply(message=message_to_dict(fake)))
    )
    spec = SandboxSpec(
        create=SandboxCreate(fs_capacity_bytes=1, idle_ttl_seconds=1, delete_after_stop_seconds=1),
        tools=["read_file"],
        backend_paths=["/blobs/"],
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
        tool_call={
            "name": "read_file",
            "args": {"file_path": "/blobs/shot.png"},
            "id": "call-1",
            "type": "tool_call",
        },
        tool=None,
        state={"messages": []},
        runtime=runtime,
    )

    result = await SandboxToolsMiddleware(backend, spec, 300, check_reads=True).awrap_tool_call(
        request, AsyncMock()
    )

    assert isinstance(result, Command)
    [message] = result.update["messages"]
    assert message.status == "error"
    assert "not a valid image" in message.content


class _ToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools: object, **kwargs: object) -> Self:
        return self


async def _launch_worker(
    monkeypatch: pytest.MonkeyPatch, client: MagicMock, *, also: Sequence[ToolCall] = ()
) -> tuple[list[ToolMessage], list[tuple[str, dict[str, object]]]]:
    """Run a coordinator that launches a worker through deepagents' ``start_async_task``."""
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
                # As `mda/agent.py` declares the worker.
                "notify_parent": False,
                "webhook": "https://backend.invalid/webhooks/run-complete?token=t",
            }
        ]
    )
    # The launch's in-process LangGraph client, the deployment's own Agent Server.
    monkeypatch.setattr(async_subagents._ClientCache, "get_async", lambda self, name: client)
    launch: ToolCall = {
        "name": "start_async_task",
        "args": {"description": "Fix login", "subagent_type": "general-purpose"},
        "id": "launch-1",
    }
    model = _ToolCallingModel(
        messages=iter([AIMessage("", tool_calls=[launch, *also]), AIMessage("Launched.")])
    )
    graph = create_agent(model, middleware=[AsyncWorkerLaunchMiddleware(backend), launches])
    values = await graph.ainvoke(
        {"messages": [("user", "fix login in the background")]},
        {
            "configurable": {"thread_id": "coordinator-thread", "assistant_id": "open-swe"},
            "metadata": {"assistant_id": "open-swe"},
        },
        # The launch middleware reads this context, though it declares no context type.
        context={"graph": "agent", "__run_token": "coordinator-token"},  # ty: ignore[invalid-argument-type]
    )
    # Open SWE's task messages report the worker; deepagents does not poll it too.
    assert not values.get("async_tasks")
    return [m for m in values["messages"] if isinstance(m, ToolMessage)], calls


async def test_a_launched_worker_carries_a_grant_never_its_coordinators_run_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.threads.create = AsyncMock(return_value={"thread_id": "worker-thread"})
    client.runs.create = AsyncMock(return_value={"run_id": "worker-run"})

    results, calls = await _launch_worker(
        monkeypatch,
        client,
        also=[
            {"name": "check_async_task", "args": {"task_id": "worker-thread"}, "id": "check-1"},
            # An update would interrupt the worker; Open SWE queues messages instead.
            {
                "name": "update_async_task",
                "args": {"task_id": "worker-thread", "message": "Stop"},
                "id": "update-1",
            },
        ],
    )

    assert client.runs.create.await_args is not None
    launch = client.runs.create.await_args.kwargs
    assert launch["context"] == {"graph": "agent", "__worker_grant": "worker-grant"}
    assert launch["input"] == {
        "messages": [{"role": "user", "content": "Your assignment: Fix login"}]
    }
    assert "coordinator-token" not in repr(client.runs.create.await_args)
    # The backend settles the launched run from its webhook, as it does every worker run.
    assert launch["webhook"] == "https://backend.invalid/webhooks/run-complete?token=t"
    # No parent assistant, so the worker never wakes the coordinator on MDA's own.
    assert client.threads.create.await_args is not None
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
    by_call = {m.tool_call_id: m for m in results}
    assert "worker-thread" in str(by_call["launch-1"].content)
    assert by_call["check-1"].status == by_call["update-1"].status == "error"
    client.runs.create.assert_awaited_once()


def _mda_error(status: int) -> APIStatusError:
    error = {429: RateLimitError, 403: PermissionDeniedError}.get(status, InternalServerError)
    response = httpx.Response(status, request=httpx.Request("POST", "http://api/threads/runs"))
    return error(f"MDA answered {status}", response=response, body=None)


@pytest.mark.parametrize(
    ("failures", "launched"),
    [
        ([_mda_error(503)], True),
        ([httpx.ConnectError("refused"), _mda_error(429)], True),
        ([_mda_error(403)], False),
        ([_mda_error(503)] * 3, False),
    ],
)
async def test_a_worker_launch_mda_failed_to_start_is_made_again_while_it_may_pass(
    monkeypatch: pytest.MonkeyPatch, failures: list[Exception], launched: bool
) -> None:
    monkeypatch.setattr("rt.middleware._LAUNCH_RETRY_SECONDS", (0.0, 0.0))
    client = MagicMock()
    client.threads.create = AsyncMock(
        side_effect=[{"thread_id": f"thread-{n}"} for n in range(len(failures) + 1)]
    )
    client.runs.create = AsyncMock(side_effect=[*failures, {"run_id": "worker-run"}])

    [result], calls = await _launch_worker(monkeypatch, client)

    attempts = [body for hook, body in calls if hook == "launch_worker"]
    # Every attempt is the same launch, so one launch still registers one worker.
    assert attempts == [{"tool_call_id": "launch-1", "instructions": "Fix login"}] * len(attempts)
    if launched:
        assert len(attempts) == len(failures) + 1
        assert result.status == "success"
        assert f"thread-{len(failures)}" in str(result.content)
        assert [hook for hook, _ in calls].count("worker_launched") == 1
    else:
        # A refusal is final; a failure that may pass is tried three times in all.
        assert len(attempts) == min(len(failures), 3)
        assert result.status == "error"
        assert "did not launch" in str(result.content)
        assert "MDA answered" in str(result.content)
        assert "start_async_task again is safe" in str(result.content)
        assert "worker_launched" not in [hook for hook, _ in calls]


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


@pytest.mark.parametrize(
    ("status", "message", "retries"),
    [
        (400, "No tool output found for function call call_1", False),
        (400, "No tool call found for function call output call_1", False),
        (400, "unexpected `tool_use_id` found in `tool_result` blocks", False),
        (400, "tool_use ids were found without `tool_result` blocks", False),
        (400, "context window exceeded", True),
        (500, "No tool output found for function call", True),
    ],
)
async def test_mda_does_not_retry_tool_pairing_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, message: str, retries: bool
) -> None:
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from rt.middleware import ModelCall
    from rt.models import ModelSpec

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    model = FakeListChatModel(responses=["done"])
    monkeypatch.setattr("rt.middleware.build_model", lambda *args, **kwargs: model)
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://provider.invalid"))
    error = (
        anthropic.BadRequestError(message, response=response, body={})
        if "tool_use" in message
        else openai.APIStatusError(message, response=response, body={})
    )
    handler = AsyncMock(side_effect=[error, ModelResponse(result=[AIMessage(content="fallback")])])
    state: RunState = {
        "messages": [],
        "open_swe_call": ModelCall(
            system_prompt="test",
            model=None,
            fallback=ModelSpec(model_id="fallback", kwargs={}),
            use_gateway=False,
        ).model_dump(mode="json"),
    }
    request = ModelRequest(model=model, messages=[], state=state)
    middleware = BackendRunMiddleware(OpenSweBackend("token"), "run")
    if retries:
        response = await middleware.awrap_model_call(request, handler)
        assert response.result[0].content == "fallback"
    else:
        with pytest.raises(type(error)) as caught:
            await middleware.awrap_model_call(request, handler)
        assert caught.value is error
