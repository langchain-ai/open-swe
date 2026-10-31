"""Native graph context schema selection and current channel authority."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict
from typing_extensions import TypedDict

from managed_deepagents import (
    DeepAgentDefinition,
    ManagedChannelContext,
    MemoryLayer,
    _run_input,
    define_memory,
    runtime,
)
from managed_deepagents._channels.binding import resolve_channel_info
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._ingress import build_ingress_run_context
from managed_deepagents._managed_thread import (
    ManagedThread,
    ThreadParticipant,
    _verified_lead_metadata,
    current_run_thread,
)
from managed_deepagents._run_input import ManagedCompiledAgent, execution_context
from managed_deepagents._thread_record import next_thread_record, thread_record_metadata


class State(TypedDict, total=False):
    observed: object
    channel: str | None


class App(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str


CHANNEL = {
    "name": "support",
    "provider": "slack",
    "source_thread_key": "source",
    "delivery_id": "delivery",
    "transport": "direct",
    "target": {"conversation_id": "C1"},
    "event": {"type": "message", "messages": []},
    "raw_event": {"channel_type": "im"},
}
CONFIG = {
    "configurable": {
        "langgraph_auth_user": {
            "mda_source_provider": "slack",
            "mda_source_thread_id": "source",
        }
    }
}
SOURCES = [SimpleNamespace(name="support", kind="slack")]


@pytest.fixture
def graph_factory(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    observed: list[object] = []

    def create(**options: Any) -> object:
        def observe(state: State, runtime: Runtime) -> dict[str, object]:
            observed.append(runtime.context)
            channel = resolve_channel_info(runtime.context, get_config()["configurable"])
            return {"observed": runtime.context, "channel": channel.name if channel else None}

        return (
            StateGraph(State, context_schema=options.get("context_schema"))
            .add_node("observe", observe)
            .add_edge(START, "observe")
            .add_edge("observe", END)
            .compile()
        )

    monkeypatch.setattr(runtime, "create_deep_agent", create)
    return observed


def test_channel_run_keeps_authored_schema_and_removes_transport(
    graph_factory: list[object],
) -> None:
    context = build_ingress_run_context(binding=CHANNEL)["context"]
    policies: list[object] = []
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition({"model": "test", "context_schema": App}),
        CONFIG,
        channels=SOURCES,
        memory=define_memory(agent=MemoryLayer(allow=lambda ctx: policies.append(ctx) or True)),
        run_context=context,
    )
    assert graph.context_schema is App
    assert isinstance(policies[0], ManagedChannelContext)
    value = graph.invoke({}, context=context)["observed"]
    assert value is None
    assert resolve_channel_info({}, CONFIG["configurable"]) is None
    assert resolve_channel_info(value, {}) is None
    assert graph_factory == [value]


def test_direct_run_keeps_authored_schema_and_native_validation(
    graph_factory: list[object],
) -> None:
    raw = {"question": "factory"}

    def factory(rt: Any) -> DeepAgentDefinition:
        assert rt.context is raw
        assert rt.channel is None
        assert rt.backend is None
        return DeepAgentDefinition({"name": "direct-app", "model": "test", "context_schema": App})

    graph = runtime.compile_managed_agent(
        factory,
        channels=SOURCES,
        run_context=raw,
    )
    assert graph.context_schema is App
    assert graph.invoke({}, context={"question": "execution"})["observed"] == App(
        question="execution"
    )
    with pytest.raises(ValueError, match="question"):
        graph.invoke({}, context={"channel": CHANNEL})


def test_channel_field_does_not_select_schema_without_source_auth(
    graph_factory: list[object],
) -> None:
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition({"model": "test"}),
        channels=SOURCES,
        run_context={"channel": CHANNEL},
    )
    assert graph.context_schema is None
    value = graph.invoke({}, context={"channel": CHANNEL})["observed"]
    assert resolve_channel_info(value, {}) is None


def test_inspection_keeps_authored_schema_and_skips_policy(graph_factory: list[object]) -> None:
    calls: list[object] = []
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition({"model": "test", "context_schema": App}),
        CONFIG,
        channels=SOURCES,
        memory=define_memory(agent=MemoryLayer(allow=lambda value: calls.append(value) or True)),
        is_run=False,
    )
    assert graph.context_schema is App
    assert "question" in graph.get_context_jsonschema()["properties"]
    assert calls == []


@pytest.mark.parametrize(
    "event",
    [
        {"type": "message", "messages": [{"role": "user", "content": "hello"}]},
        {"type": "interrupt_resume", "resume": {"interrupt": True}},
        {"type": "user_prompt_response", "action": "approve"},
    ],
)
def test_channel_transport_serializes_but_is_absent_in_native_context(
    graph_factory: list[object], event: dict[str, object]
) -> None:
    channel = {key: value for key, value in CHANNEL.items() if key != "raw_event"}
    channel["event"] = event
    context = build_ingress_run_context(binding=channel)["context"]
    assert context["mda_channel"]["sourceThreadKey"] == "source"
    serialized = json.dumps(context, allow_nan=False)
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition({"model": "test", "context_schema": App}),
        CONFIG,
        channels=SOURCES,
        run_context=context,
    )
    restored = graph.invoke({}, context=json.loads(serialized))["observed"]
    assert restored is None


@pytest.mark.parametrize("use_async", [False, True])
def test_graph_copy_and_resume_use_current_delivery(use_async: bool) -> None:
    seen: list[str] = []

    def node(state: State, runtime: Runtime) -> dict[str, object]:
        assert runtime.context is None
        channel = resolve_channel_info(runtime.context, get_config()["configurable"])
        seen.append(channel.event["type"])
        interrupt("continue")
        return {"observed": channel.event["type"]}

    graph = (
        StateGraph(State, context_schema=App)
        .add_node("observe", node)
        .add_edge(START, "observe")
        .add_edge("observe", END)
        .compile(checkpointer=InMemorySaver())
    )
    graph.__class__ = ManagedCompiledAgent
    graph = graph.with_config(
        {**CONFIG, "configurable": {**CONFIG["configurable"], "thread_id": "resume"}}
    )
    # Register the same declared source used by compile_managed_agent.
    register_channel_messaging(SOURCES)

    def invoke(input: object, context: object) -> Any:
        return (
            asyncio.run(graph.ainvoke(input, context=context))
            if use_async
            else graph.invoke(input, context=context)
        )

    first = invoke({}, {"mda_channel": CHANNEL})
    assert first["__interrupt__"]
    resumed = {
        **CHANNEL,
        "delivery_id": "resume",
        "event": {"type": "interrupt_resume", "resume": {"answer": True}},
    }
    resumed.pop("raw_event")
    assert invoke(Command(resume=True), {"mda_channel": resumed})["observed"] == "interrupt_resume"
    assert seen == ["message", "interrupt_resume"]
    assert resolve_channel_info(None, CONFIG["configurable"]) is None


def test_concurrent_bindings_and_nested_direct_execution_are_isolated() -> None:
    register_channel_messaging(SOURCES)

    async def run() -> None:
        arrived = 0
        barrier = asyncio.Event()

        def direct_node(state: State, runtime: Runtime) -> dict[str, object]:
            assert runtime.context == App(question="nested")
            assert resolve_channel_info(None, get_config()["configurable"]) is None
            return {"observed": runtime.context.question}

        direct = (
            StateGraph(State, context_schema=App)
            .add_node("direct", direct_node)
            .add_edge(START, "direct")
            .add_edge("direct", END)
            .compile()
        )
        direct.__class__ = ManagedCompiledAgent

        async def node(state: State, runtime: Runtime) -> dict[str, object]:
            nonlocal arrived
            assert runtime.context is None
            configurable = get_config()["configurable"]
            before = resolve_channel_info(None, configurable)
            arrived += 1
            if arrived == 2:
                barrier.set()
            await barrier.wait()
            assert (
                await direct.ainvoke(
                    {},
                    {"configurable": {"langgraph_auth_user": {}}},
                    context={"question": "nested"},
                )
            )["observed"] == "nested"
            after = resolve_channel_info(None, configurable)
            assert before == after
            return {"observed": after.raw_event["label"]}

        graph = (
            StateGraph(State, context_schema=App)
            .add_node("channel", node)
            .add_edge(START, "channel")
            .add_edge("channel", END)
            .compile()
        )
        graph.__class__ = ManagedCompiledAgent
        copied = graph.with_config(CONFIG)
        results = await asyncio.gather(
            *(
                copied.ainvoke(
                    {}, context={"mda_channel": {**CHANNEL, "raw_event": {"label": label}}}
                )
                for label in ["first", "second"]
            )
        )
        assert [result["observed"] for result in results] == ["first", "second"]
        assert resolve_channel_info(None, CONFIG["configurable"]) is None

    asyncio.run(run())


def test_unverified_source_has_no_channel_capability(graph_factory: list[object]) -> None:
    calls: list[object] = []

    def factory(rt: Any) -> DeepAgentDefinition:
        calls.append(rt)
        return DeepAgentDefinition({"name": "source-check", "model": "test"})

    runtime.compile_managed_agent(
        factory,
        CONFIG,
        channels=SOURCES,
        run_context={"mda_channel": {**CHANNEL, "source_thread_key": "wrong"}},
    )
    assert calls[0].context is None
    assert calls[0].channel is None


@pytest.mark.parametrize("use_async", [False, True])
def test_channel_child_does_not_inherit_direct_parent_context(use_async: bool) -> None:
    register_channel_messaging(SOURCES)

    def child_node(state: State, runtime: Runtime) -> dict[str, object]:
        assert runtime.context is None
        channel = resolve_channel_info(None, get_config()["configurable"])
        assert channel.name == "support"
        return {"observed": channel.name}

    child = (
        StateGraph(State, context_schema=App)
        .add_node("child", child_node)
        .add_edge(START, "child")
        .add_edge("child", END)
        .compile()
    )
    child.__class__ = ManagedCompiledAgent

    def parent_node(state: State, runtime: Runtime) -> dict[str, object]:
        result = child.invoke({}, CONFIG, context={"mda_channel": CHANNEL})
        assert runtime.context == App(question="parent")
        return result

    async def async_parent_node(state: State, runtime: Runtime) -> dict[str, object]:
        result = await child.ainvoke({}, CONFIG, context={"mda_channel": CHANNEL})
        assert runtime.context == App(question="parent")
        return result

    parent = (
        StateGraph(State, context_schema=App)
        .add_node("parent", async_parent_node if use_async else parent_node)
        .add_edge(START, "parent")
        .add_edge("parent", END)
        .compile()
    )
    result = (
        asyncio.run(parent.ainvoke({}, context={"question": "parent"}))
        if use_async
        else parent.invoke({}, context={"question": "parent"})
    )
    assert result["observed"] == "support"


@pytest.mark.parametrize("use_async", [False, True])
def test_explicit_thread_keeps_native_direct_context_under_channel_parent(use_async: bool) -> None:
    register_channel_messaging(SOURCES)

    def child_node(state: State, runtime: Runtime) -> dict[str, object]:
        assert runtime.context == App(question="direct")
        assert get_config()["configurable"].get("langgraph_auth_user") is None
        return {"observed": runtime.context.question}

    child = (
        StateGraph(State, context_schema=App)
        .add_node("child", child_node)
        .add_edge(START, "child")
        .add_edge("child", END)
        .compile()
    )
    child.__class__ = ManagedCompiledAgent
    direct_config = {"configurable": {"thread_id": "new"}}

    def parent_node(state: State, runtime: Runtime) -> dict[str, object]:
        assert runtime.context is None
        return child.invoke({}, direct_config, context={"question": "direct"})

    async def async_parent_node(state: State, runtime: Runtime) -> dict[str, object]:
        assert runtime.context is None
        return await child.ainvoke({}, direct_config, context={"question": "direct"})

    parent = (
        StateGraph(State, context_schema=App)
        .add_node("parent", async_parent_node if use_async else parent_node)
        .add_edge(START, "parent")
        .add_edge("parent", END)
        .compile()
    )
    parent.__class__ = ManagedCompiledAgent
    result = (
        asyncio.run(parent.ainvoke({}, CONFIG, context={"mda_channel": CHANNEL}))
        if use_async
        else parent.invoke({}, CONFIG, context={"mda_channel": CHANNEL})
    )
    assert result["observed"] == "direct"


class ThreadState(TypedDict, total=False):
    messages: list[object]


RECORD = thread_record_metadata(next_thread_record(None, CHANNEL, "alice"))
NO_SOURCE = {"configurable": {"langgraph_auth_user": {"identity": "studio"}}}


def _thread_graph(seen: list[ManagedThread]) -> Any:
    def node(state: ThreadState) -> dict[str, object]:
        seen.append(current_run_thread(None, get_config()["configurable"]).thread)
        return {}

    graph = (
        StateGraph(ThreadState)
        .add_node("observe", node)
        .add_edge(START, "observe")
        .add_edge("observe", END)
        .compile(checkpointer=InMemorySaver())
    )
    graph.__class__ = ManagedCompiledAgent
    register_channel_messaging(SOURCES)
    return graph


def test_a_run_without_a_channel_message_reads_the_thread_record() -> None:
    # A Studio resume, or a task notification waking the agent: no live binding.
    seen: list[ManagedThread] = []
    graph = _thread_graph(seen)
    config = {
        "metadata": RECORD,
        "configurable": {**NO_SOURCE["configurable"], "thread_id": "thread-1"},
    }
    with patch.object(_run_input, "deliver_run_result", AsyncMock()):
        asyncio.run(graph.ainvoke({"messages": []}, config))

    (thread,) = seen
    assert thread.id == "thread-1"
    assert thread.participants == [ThreadParticipant(id="alice", role="owner")]
    assert thread.origin is not None
    assert thread.origin.sender == ThreadParticipant(id="alice", role="owner")
    assert (thread.origin.channel.name, thread.origin.channel.provider) == ("support", "slack")
    assert thread.origin.channel.event is None
    assert thread.origin.channel.raw_event is None


def _helper_config(user: object = None) -> dict[str, object]:
    return {
        "run_id": "22222222-2222-4222-8222-222222222222",
        "configurable": {
            "langgraph_auth_user": user or CONFIG["configurable"]["langgraph_auth_user"],
            "thread_id": "helper",
            "deepagents_parent": {"thread_id": "lead"},
        },
    }


@pytest.mark.parametrize("verified", [True, False])
def test_an_async_subagent_runs_on_its_leads_thread(verified: bool) -> None:
    # `parent_run` keeps the lead thread's metadata once its owner check passes.
    seen: list[ManagedThread] = []
    graph = _thread_graph(seen)
    token = _verified_lead_metadata.set(("lead", RECORD) if verified else None)
    try:
        graph.invoke({"messages": []}, _helper_config())
    finally:
        _verified_lead_metadata.reset(token)

    (thread,) = seen
    assert thread.id == "lead"
    assert (thread.origin is not None) is verified
    assert bool(thread.participants) is verified


@pytest.mark.parametrize("with_channel", [True, False])
def test_scheduled_run_strips_routing_and_delivers_the_rest(
    graph_factory: list[object], with_channel: bool
) -> None:
    user = CONFIG["configurable"]["langgraph_auth_user"] if with_channel else {"mda_user_id": "a"}
    config = {"configurable": {"langgraph_auth_user": {**user, "mda_scheduled_run": True}}}
    routing = {
        "mda_schedule": {"name": "daily"},
        **({"mda_channel": CHANNEL} if with_channel else {}),
    }
    seen: list[object] = []

    def factory(rt: Any) -> DeepAgentDefinition:
        seen.append((rt.context, rt.channel.name if rt.channel else None))
        return DeepAgentDefinition({"name": "scheduled", "model": "test", "context_schema": App})

    graph = runtime.compile_managed_agent(
        factory, config, channels=SOURCES, run_context={**routing, "question": "docs"}
    )
    assert seen == [({"question": "docs"}, "support" if with_channel else None)]
    assert graph.invoke({}, config, context={**routing, "question": "docs"})["observed"] == App(
        question="docs"
    )
    assert graph.invoke({}, config, context=routing)["observed"] is None
    with pytest.raises(ValueError, match="question"):
        graph.invoke({}, config, context={**routing, "question": 1})


SCHEDULE_CONFIG = {
    **CONFIG["configurable"],
    "langgraph_auth_user": {
        **CONFIG["configurable"]["langgraph_auth_user"],
        "mda_scheduled_run": True,
    },
    "mda_schedule": {"name": "daily"},
    "channel": CHANNEL,
}


@pytest.mark.parametrize(
    ("config", "context", "authored", "bound"),
    [
        (SCHEDULE_CONFIG, None, None, True),
        (SCHEDULE_CONFIG, SCHEDULE_CONFIG, None, True),
        (SCHEDULE_CONFIG, {"channel": CHANNEL}, None, True),
        (
            SCHEDULE_CONFIG,
            {"mda_channel": CHANNEL, "channel": "authored"},
            {"channel": "authored"},
            True,
        ),
        (
            {**SCHEDULE_CONFIG, "mda_channel": CHANNEL, "channel": "authored"},
            {"channel": "authored"},
            {"channel": "authored"},
            True,
        ),
        (
            {**CONFIG["configurable"], "channel": CHANNEL},
            {"channel": "authored"},
            {"channel": "authored"},
            False,
        ),
    ],
)
def test_managed_routing(
    config: dict[str, object], context: object, authored: object, bound: bool
) -> None:
    register_channel_messaging(SOURCES)
    binding, actual, _ = execution_context(context, {"configurable": config}, None)
    assert actual == authored
    assert (binding is not None) is bound
    if binding:
        assert binding["source_thread_key"] == "source"


@pytest.mark.parametrize("scheduled", [False, True])
def test_factory_routing_survives_filtered_context(
    graph_factory: list[object], scheduled: bool
) -> None:
    config = {
        "configurable": {
            "langgraph_auth_user": {
                **CONFIG["configurable"]["langgraph_auth_user"],
                "mda_scheduled_run": scheduled,
            }
        }
    }
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition({"model": "test", "context_schema": App}),
        config,
        channels=SOURCES,
        run_context={"mda_channel": CHANNEL, "question": "docs"},
    )
    # Agent Server filters transport keys after the factory returns.
    result = graph.invoke({}, config, context={"question": "docs"})
    assert result == {"observed": App(question="docs"), "channel": "support"}


# A run with no live channel gives its memory policy the thread origin's channel.


NOTIFICATION_CONFIG = {
    "metadata": RECORD,
    "configurable": {**CONFIG["configurable"], "thread_id": "lead", "run_id": "wake"},
}


def test_memory_policy_sees_the_thread_origins_channel(graph_factory: list[object]) -> None:
    policies: list[object] = []
    runtime.compile_managed_agent(
        DeepAgentDefinition({"model": "test"}),
        NOTIFICATION_CONFIG,
        channels=SOURCES,
        memory=define_memory(agent=MemoryLayer(allow=lambda ctx: policies.append(ctx) or True)),
        run_context={},
    )

    (policy_context,) = policies
    assert isinstance(policy_context, ManagedChannelContext)
    assert policy_context.channel["provider"] == "slack"
    assert "event" not in policy_context.channel
