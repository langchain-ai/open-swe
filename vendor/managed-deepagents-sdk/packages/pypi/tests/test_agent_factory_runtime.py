"""Runtime resolution tests for dynamic managed agent entries."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.runtime import ServerRuntime, _ExecutionRuntime, _ReadRuntime

from managed_deepagents import ManagedRunContext, define_browser
from managed_deepagents import runtime as runtime_mod
from managed_deepagents.define_deep_agent import DeepAgentDefinition, define_deep_agent
from managed_deepagents.define_memory import MemoryLayer, define_memory


def resolve(entry: object, context: dict[str, object] | None = None) -> DeepAgentDefinition:
    return asyncio.run(
        runtime_mod.resolve_agent_definition(
            entry, runtime_mod._internal_server_runtime(None, context or {}, True)
        )  # type: ignore[arg-type]
    )


def test_resolver_returns_static_definition_unchanged() -> None:
    definition = define_deep_agent(name="static")

    assert resolve(definition) is definition


def test_resolver_rejects_structural_static_definition() -> None:
    definition = SimpleNamespace(config={"model": "test-model"})

    with pytest.raises(TypeError, match="managed agent definition or factory entrypoint"):
        resolve(definition)


def test_resolver_passes_context_to_sync_factory() -> None:
    def build(runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        context = runtime.execution_runtime.context if runtime.execution_runtime is not None else {}
        model = "openai:gpt-5-mini" if context.get("tier") == "fast" else "openai:gpt-5"
        return define_deep_agent(name="selected", model=model)

    selected = resolve(build, {"tier": "fast"})

    assert selected.config["model"] == "openai:gpt-5-mini"


def test_resolver_awaits_async_factory() -> None:
    async def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return define_deep_agent(name="async")

    assert resolve(build).config["name"] == "async"


def test_resolver_snapshots_returned_definition() -> None:
    selected = define_deep_agent(name="selected")

    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return selected

    resolved = resolve(build)
    selected.config["name"] = "mutated"

    assert resolved.config["name"] == "selected"


def test_resolver_rejects_invalid_result() -> None:
    def build(_runtime: ServerRuntime[ManagedRunContext]) -> Any:
        return None

    with pytest.raises(TypeError, match=r"must return define_deep_agent\(\.\.\.\)"):
        resolve(build)


def test_resolver_hides_factory_exception() -> None:
    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        raise RuntimeError("private callback details")

    with pytest.raises(RuntimeError) as exc_info:
        resolve(build)

    assert str(exc_info.value) == "agent factory failed"


def test_resolver_rejects_forged_managed_field_without_value() -> None:
    private_value = "private backend value"

    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return DeepAgentDefinition({"name": "forged", "backend": private_value})

    with pytest.raises(ValueError, match="agent factory returned") as exc_info:
        resolve(build)

    assert str(exc_info.value) == "agent factory returned a managed runtime field"
    assert private_value not in str(exc_info.value)


def test_resolver_rejects_memory_on_manual_definition() -> None:
    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return DeepAgentDefinition({"name": "forged-memory", "memory": ["agent"]})

    with pytest.raises(TypeError) as exc_info:
        resolve(build)

    assert str(exc_info.value) == "agent factory returned an invalid definition"


def test_resolver_accepts_dynamic_memory_selection() -> None:
    memory = define_memory(agent=MemoryLayer())

    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return define_deep_agent(name="selected-memory", memory=memory)

    assert resolve(build).config["memory"].config["scope"] == [{"type": "agent"}]


def test_resolver_rejects_allow_in_dynamic_memory_selection() -> None:
    memory = define_memory(agent=MemoryLayer(allow=lambda _context: True))

    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return define_deep_agent(name="selected-memory", memory=memory)

    with pytest.raises(ValueError, match="Dynamically selected memory cannot use `allow`"):
        resolve(build)


def test_resolver_revalidates_dynamic_selections_without_exposing_values() -> None:
    private_value = "private/integration"

    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return DeepAgentDefinition({"name": "forged-selection", "mcp": [private_value]})

    with pytest.raises(TypeError) as exc_info:
        resolve(build)

    assert str(exc_info.value) == "agent factory returned an invalid definition"
    assert private_value not in str(exc_info.value)


def test_resolver_snapshots_dynamic_selections() -> None:
    files = {"references/checklist.md": "Original"}
    selected = DeepAgentDefinition(
        {
            "name": "snapshot",
            "skills": [
                {
                    "name": "generated",
                    "description": "Generated skill",
                    "markdown": "# Generated",
                    "files": files,
                }
            ],
        }
    )

    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return selected

    resolved = resolve(build)
    files["references/checklist.md"] = "Changed"

    assert resolved.config["skills"] == (
        {
            "name": "generated",
            "description": "Generated skill",
            "markdown": "# Generated",
            "files": {"references/checklist.md": "Original"},
        },
    )


@pytest.fixture
def compiled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_compile(
        definition: DeepAgentDefinition,
        _config: Mapping[str, object] | None = None,
        **kwargs: object,
    ) -> tuple[object, object]:
        calls.append({"definition": definition, **kwargs})
        # The graph and its telemetry configuration.
        return object(), object()

    monkeypatch.setattr(runtime_mod, "_compile_managed_agent", fake_compile)
    return calls


def _compile_browser_selection(selection: dict[str, object], declared: object) -> None:
    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return define_deep_agent(name="browsing", **cast("Any", selection))

    asyncio.run(
        runtime_mod.compile_managed_agent_async(
            build, browser=cast("Any", declared), is_run=True, report_telemetry=False
        )
    )


def test_a_factory_keeps_turns_off_or_replaces_the_declared_browser(
    compiled: list[dict[str, object]],
) -> None:
    declared, users = define_browser(), define_browser(scope="user")
    for selection in ({}, {"browser": None}, {"browser": users}):
        _compile_browser_selection(selection, declared)
    assert [call["browser"] for call in compiled] == [declared, None, users]


def test_a_factory_needs_browser_py_to_add_a_browser(compiled: list[dict[str, object]]) -> None:
    # browser.py is what installs Stagehand in the deployment.
    with pytest.raises(ValueError, match=r"browser\.py"):
        _compile_browser_selection({"browser": define_browser()}, None)
    assert compiled == []


def test_compile_uses_authenticated_channel_context(
    compiled: list[dict[str, object]],
) -> None:
    contexts: list[object] = []
    channel_middleware = object()

    def build(runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        context = runtime.execution_runtime.context if runtime.execution_runtime is not None else {}
        contexts.append(context)
        return define_deep_agent(name="channel", middleware=[channel_middleware])  # type: ignore[list-item]

    channel_context = {
        "mda_channel": {
            "name": "factory-support",
            "provider": "slack",
            "source_thread_key": "thread-1",
            "delivery_id": "delivery",
            "transport": "direct",
            "target": {"conversation_id": "C1"},
            "event": {"type": "message", "messages": []},
        }
    }
    config = {
        "configurable": {
            "langgraph_auth_user": {
                "mda_source_provider": "slack",
                "mda_source_thread_id": "thread-1",
            }
        }
    }
    asyncio.run(
        runtime_mod.compile_managed_agent_async(
            build,
            config,
            run_context=channel_context,
            channels=[SimpleNamespace(name="factory-support", kind="slack")],
            is_run=True,
            report_telemetry=False,
        )
    )

    assert contexts == [None]
    assert compiled[0]["context_schema"] is None
    selected = compiled[0]["definition"]
    assert isinstance(selected, DeepAgentDefinition)
    assert selected.config["middleware"] == [channel_middleware]


def test_compile_uses_empty_inspection_context_and_skips_tools(
    compiled: list[dict[str, object]],
) -> None:
    contexts: list[object] = []

    def build(runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        context = runtime.execution_runtime.context if runtime.execution_runtime is not None else {}
        contexts.append(context)
        return define_deep_agent(name="inspection")

    asyncio.run(
        runtime_mod.compile_managed_agent_async(
            build,
            run_context={"private": "ignored"},
            is_run=False,
            report_telemetry=False,
        )
    )

    assert contexts == [{}]
    assert compiled[0]["skip_factory_inspection_tools"] is True


def test_compile_resolves_again_for_each_invocation(
    compiled: list[dict[str, object]],
) -> None:
    calls = 0

    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        nonlocal calls
        calls += 1
        return define_deep_agent(name=f"invocation-{calls}")

    for _ in range(2):
        asyncio.run(
            runtime_mod.compile_managed_agent_async(
                build,
                run_context={},
                report_telemetry=False,
            )
        )

    assert calls == 2
    assert [
        call["definition"].config["name"]  # type: ignore[union-attr]
        for call in compiled
    ] == ["invocation-1", "invocation-2"]


@pytest.mark.parametrize("async_factory", [False, True])
def test_compile_forwards_native_server_runtime(
    compiled: list[dict[str, object]], async_factory: bool
) -> None:
    context = {"tier": "fast"}
    server_runtime = _ExecutionRuntime(
        access_context="threads.create_run",
        user=SimpleNamespace(identity="caller"),
        store=InMemoryStore(),
        context=context,
    )
    received = []

    def build(runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        received.append(runtime)
        return define_deep_agent(name="native-factory")

    async def async_build(runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return build(runtime)

    entry = async_build if async_factory else build
    asyncio.run(
        runtime_mod.compile_managed_agent_async(
            entry, runtime=server_runtime, report_telemetry=False
        )
    )
    assert received[0].context is context
    assert received[0].user is server_runtime.user
    assert received[0].store is server_runtime.store
    assert received[0].access_context == server_runtime.access_context
    assert received[0].channel is None
    assert received[0].backend is None
    assert received[0].execution_runtime is received[0]
    assert compiled[0]["inspection"] is False


def test_plain_factory_inspection_skips_resources_and_memory(
    compiled: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    server_runtime = _ReadRuntime(access_context="assistants.read", store=InMemoryStore())
    received = []

    def build(runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        received.append(runtime)
        return define_deep_agent(name="native-inspection")

    def forbidden_policy(*args: object, **kwargs: object) -> None:
        pytest.fail("inspection must not resolve memory policy")

    monkeypatch.setattr(runtime_mod, "resolve_agent_memory_access", forbidden_policy)
    monkeypatch.setattr(runtime_mod, "resolve_user_memory_access", forbidden_policy)
    asyncio.run(
        runtime_mod.compile_managed_agent_async(
            build,
            runtime=server_runtime,
            run_context={"private": "ignored"},
            report_telemetry=False,
        )
    )
    assert received[0].store is server_runtime.store
    assert received[0].access_context == server_runtime.access_context
    assert received[0].execution_runtime is None
    assert compiled[0]["inspection"] is True
    assert compiled[0]["skip_factory_inspection_tools"] is True


def test_internal_runtime_has_native_access_and_store() -> None:
    runtime = runtime_mod._internal_server_runtime(None, {"tier": "fast"}, True)
    assert runtime.access_context == "threads.create_run"
    assert runtime.execution_runtime is not None
    assert runtime.execution_runtime.context == {"tier": "fast"}
    assert isinstance(runtime.store, BaseStore)
    with pytest.raises(PermissionError):
        runtime.ensure_user()


def test_internal_inspection_runtime_does_not_grant_identity() -> None:
    runtime = runtime_mod._internal_server_runtime(
        {"configurable": {"langgraph_auth_user": {"identity": "unverified"}}},
        {"private": "ignored"},
        False,
    )
    assert runtime.access_context == "assistants.read"
    assert runtime.execution_runtime is None
    assert runtime.user is None
    with pytest.raises(PermissionError):
        runtime.ensure_user()


def test_plain_callable_config_attribute_does_not_bypass_factory_validation() -> None:
    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        return define_deep_agent(name="callable")

    build.config = {"name": "forged", "backend": object()}  # type: ignore[attr-defined]
    selected = resolve(build)
    assert isinstance(selected, DeepAgentDefinition)
    assert selected.config["name"] == "callable"
