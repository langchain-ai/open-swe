"""Check managed adaptation with native LangChain and LangGraph runtimes."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass, fields
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, AgentState, before_model
from langchain.tools import ToolRuntime
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_config
from langgraph.runtime import Runtime, ServerInfo
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.auth.types import StudioUser
from langgraph_sdk.runtime import _ExecutionRuntime
from pydantic import BaseModel, ConfigDict

from managed_deepagents import (
    DeepAgentDefinition,
    ManagedDeepAgentRuntime,
    ManagedRuntime,
    ManagedToolRuntime,
)
from managed_deepagents import runtime as managed
from managed_deepagents._managed_middleware import with_managed_middleware
from managed_deepagents._managed_tools import (
    adapt_managed_runtime,
    managed_runtime_overrides,
    with_managed_runtime,
)
from managed_deepagents._schedule_channel import schedule_thread


@dataclass
class Context:
    team: str


class AppContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str


class ChannelAppContext(AppContext):
    channel: str


class _Model(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "native-runtime-test"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        result = (
            AIMessage(content="done")
            if isinstance(messages[-1], ToolMessage)
            else AIMessage(
                content="",
                tool_calls=[{"id": "call", "name": "count", "args": {"value": 2}}],
            )
        )
        return ChatResult(generations=[ChatGeneration(message=result)])


def test_native_tool_runtime_fields_and_methods_survive_without_auth() -> None:
    store = InMemoryStore()
    user = StudioUser("caller")
    native = ToolRuntime(
        state={"count": 2},
        context={"team": "research"},
        config={"tags": ["native"]},
        stream_writer=lambda _: None,
        tool_call_id="call",
        store=store,
        server_info=ServerInfo("assistant", "graph", user=user),
    )
    managed = adapt_managed_runtime(native, managed_runtime_overrides(native))
    assert isinstance(managed, ManagedToolRuntime)
    assert isinstance(managed, ToolRuntime)
    assert ManagedDeepAgentRuntime is ManagedToolRuntime
    for field in fields(native):
        if field.name != "server_info":
            assert getattr(managed, field.name) is getattr(native, field.name)
    assert managed.server_info is not None
    assert isinstance(managed.server_info, ServerInfo)
    assert managed.server_info.assistant_id == "assistant"
    assert managed.server_info.user is user
    assert managed.server_info.graph_id == "graph"
    assert managed.server_info.principal is None
    assert managed.backend is None
    assert managed.channel is None
    managed.emit_output_delta({"delta": "supported"})


def test_native_middleware_runtime_fields_and_behavior_survive() -> None:
    beats: list[bool] = []
    native = Runtime(
        context={"team": "research"}, previous={"count": 2}, heartbeat=lambda: beats.append(True)
    )
    managed = adapt_managed_runtime(native, managed_runtime_overrides(native))
    assert isinstance(managed, ManagedRuntime)
    assert isinstance(managed, Runtime)
    assert not isinstance(managed, ToolRuntime)
    for field in fields(native):
        assert getattr(managed, field.name) is getattr(native, field.name)
    managed.heartbeat()
    assert beats == [True]
    assert managed.override(context={"team": "support"}).context == {"team": "support"}
    assert not managed.drain_requested
    assert not hasattr(managed, "config")
    assert managed.channel is None
    assert managed.backend is None


def test_default_package_import_does_not_require_mcp_extras() -> None:

    # An absent optional package has a None module entry. This also prevents an
    # installed contributor extra from hiding the default-install regression.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.modules['fastmcp'] = None; "
                "import managed_deepagents; import managed_deepagents.runtime"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_middleware_lifecycle_model_and_tool_hooks_receive_native_subclasses() -> None:

    seen: dict[str, list[object]] = {}
    store = InMemoryStore()

    @tool
    def count(value: int, runtime: ManagedToolRuntime[Context, AgentState]) -> str:
        """Read the exact custom context in a native managed tool."""
        assert isinstance(runtime, ToolRuntime)
        assert isinstance(runtime, ManagedToolRuntime)
        assert runtime.context.team == "research"
        assert runtime.store is store
        assert runtime.tool_call_id == "call"
        assert "user_id" not in runtime.config["configurable"]
        runtime.emit_output_delta({"count": value})
        seen.setdefault("tool", []).append(runtime)
        return str(value)

    class Audit(AgentMiddleware[AgentState, Context]):
        def before_model(self, state: AgentState, runtime: Runtime[Context]) -> None:
            assert isinstance(runtime, ManagedRuntime)
            assert not isinstance(runtime, ToolRuntime)
            assert runtime.context.team == "research"
            assert runtime.store is store
            assert not hasattr(runtime, "config")
            runtime.heartbeat()
            assert runtime.override(context=Context("support")).context.team == "support"
            seen.setdefault("lifecycle", []).append(runtime)

        def wrap_model_call(self, request: Any, handler: Any) -> Any:
            assert isinstance(request.runtime, ManagedRuntime)
            seen.setdefault("model", []).append(request.runtime)
            return handler(request)

        def wrap_tool_call(self, request: Any, handler: Any) -> Any:
            assert isinstance(request.runtime, ManagedToolRuntime)
            assert request.runtime.context.team == "research"
            seen.setdefault("wrap_tool", []).append(request.runtime)
            return handler(request)

    @before_model
    def free_hook(state: AgentState, runtime: ManagedRuntime[Context]) -> None:
        assert isinstance(runtime, ManagedRuntime)
        seen.setdefault("decorator", []).append(runtime)

    agent = create_agent(
        _Model(),
        tools=[with_managed_runtime(count)],
        context_schema=Context,
        store=store,
        middleware=[with_managed_middleware(Audit()), with_managed_middleware(free_hook)],
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "count"}]},
        {
            "configurable": {
                "thread_id": "native",
                "user_id": "spoof",
                "langgraph_auth_user": {"identity": "caller", "mda_user_id": "caller"},
            }
        },
        context=Context("research"),
    )
    assert result["messages"][-1].content == "done"
    assert set(seen) == {"lifecycle", "model", "tool", "wrap_tool", "decorator"}
    for runtimes in seen.values():
        for runtime in runtimes:
            assert runtime.server_info.principal.id == "caller"
            assert runtime.backend is None
            assert runtime.channel is None


def test_tool_runtime_injection_accepts_other_parameter_names() -> None:
    @tool
    def count(value: int, rt: ManagedToolRuntime[Context, AgentState]) -> str:
        """Read run context through a named injected runtime."""
        assert isinstance(rt, ManagedToolRuntime)
        assert rt.context.team == "research"
        assert rt.backend is None
        assert rt.channel is None
        return str(value)

    agent = create_agent(_Model(), tools=[with_managed_runtime(count)], context_schema=Context)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "count"}]}, context=Context("research")
    )
    assert result["messages"][-1].content == "done"


@pytest.mark.parametrize("authored", [False, True])
@pytest.mark.parametrize("use_async", [False, True])
def test_managed_channel_runtime_keeps_application_context_separate_from_delivery(
    use_async: bool,
    authored: bool,
) -> None:
    app_context = {"team": "research", "channel": "application-value"} if authored else None
    schema = ChannelAppContext if authored else AppContext
    seen: list[str] = []

    posted: list[object] = []

    class Messaging:
        async def post(self, **request: object) -> dict[str, str]:
            posted.append(request)
            return {"id": "reply"}

    def check(rt: Any, stage: str) -> None:
        expected = (
            (app_context if stage == "factory" else ChannelAppContext(**app_context))
            if authored
            else None
        )
        assert rt.context == expected
        if stage != "factory":
            # A schedule made here posts to the thread's origin: this delivery's.
            origin = schedule_thread(get_config()["configurable"]).origin
            assert origin is not None
            assert origin["provider"] == "orders"
            assert origin["target"] == {"conversation_id": "C1"}
        assert rt.thread.origin is not None
        assert rt.channel is rt.thread.origin.channel
        assert rt.channel.name == "native-support"
        assert rt.channel.provider == "orders"
        assert rt.backend is None
        seen.append(stage)

    @tool
    def count(value: int, runtime: ManagedToolRuntime[AppContext, AgentState]) -> str:
        """Check the native tool runtime on a channel run."""
        check(runtime, "tool")
        asyncio.run(runtime.channel.post({"type": "content", "content": "tool"}))
        return str(value)

    class Audit(AgentMiddleware):
        def before_model(self, state: AgentState, runtime: Runtime) -> None:
            check(runtime, "middleware")
            assert isinstance(runtime, ManagedRuntime)

    async def factory(rt: Any) -> DeepAgentDefinition:
        check(rt, "factory")
        await rt.channel.post({"type": "content", "content": "factory"})
        assert rt.execution_runtime is rt
        assert isinstance(rt, _ExecutionRuntime)
        return DeepAgentDefinition(
            {
                "name": "native-channel",
                "model": _Model(),
                "tools": [count],
                "middleware": [Audit()],
                "context_schema": schema,
            }
        )

    context = {
        **(app_context or {}),
        "mda_channel": {
            "name": "native-support",
            "provider": "orders",
            "source_thread_key": "source",
            "delivery_id": "delivery",
            "transport": "direct",
            "target": {"conversation_id": "C1"},
            "event": {"type": "message", "messages": []},
        },
    }
    config = {
        "configurable": {
            "langgraph_auth_user": {
                "mda_source_provider": "orders",
                "mda_source_thread_id": "source",
            }
        }
    }
    graph = managed.compile_managed_agent(
        factory,
        config,
        run_context=context,
        report_telemetry=False,
        channels=[
            SimpleNamespace(name="native-support", kind="orders", messaging=lambda env: Messaging())
        ],
    ).with_config({"tags": ["copy"]})
    assert graph.context_schema is schema
    # The Agent Server keeps the run's state, which the run reads to post its answer.
    graph.checkpointer = InMemorySaver()
    thread = {"configurable": {"thread_id": "native"}}
    input = {"messages": [{"role": "user", "content": "count"}]}
    result = (
        asyncio.run(graph.ainvoke(input, thread, context=context))
        if use_async
        else graph.invoke(input, thread, context=context)
    )
    assert result["messages"][-1].content == "done"
    assert seen == ["factory", "middleware", "tool", "middleware"]
    # The factory's and the tool's posts, then the run posting its own answer.
    assert [item["target"] for item in posted] == [{"conversation_id": "C1"}] * 3
    assert posted[-1]["message"] == {"type": "content", "content": "done"}
