"""Eval compile must write Harbor trial files to real disk under /app."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest
from langgraph_sdk.runtime import ServerRuntime

from managed_deepagents import define_sandbox
from managed_deepagents import runtime as runtime_mod
from managed_deepagents._connector import Connector
from managed_deepagents._connectors.mcp import McpServersDefinition
from managed_deepagents.define_deep_agent import DeepAgentDefinition, define_deep_agent


class _FakeFilesystemBackend:
    instances: ClassVar[list[_FakeFilesystemBackend]] = []

    def __init__(self, *, root_dir: str | None = None, virtual_mode: bool = False) -> None:
        self.root_dir = root_dir
        self.virtual_mode = virtual_mode
        self.__class__.instances.append(self)

    def _resolve_path(self, key: str) -> str:
        root = self.root_dir or ""
        if self.virtual_mode:
            vpath = key if key.startswith("/") else f"/{key}"
            return f"{root}/{vpath[1:]}".replace("//", "/")
        return key


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    _FakeFilesystemBackend.instances.clear()
    store: dict[str, Any] = {}

    class _FakeAgent:
        def with_config(self, config: dict[str, Any]) -> _FakeAgent:
            store["with_config"] = config
            return self

    def _fake_create_deep_agent(**kwargs: Any) -> Any:
        store["params"] = kwargs
        return _FakeAgent()

    monkeypatch.setattr(runtime_mod, "FilesystemBackend", _FakeFilesystemBackend)
    monkeypatch.setattr(runtime_mod, "create_deep_agent", _fake_create_deep_agent)
    return store


def test_eval_compile_uses_filesystem_backend_under_app(captured: dict[str, Any]) -> None:
    definition = DeepAgentDefinition({"model": "openai:gpt-5-mini"})
    runtime_mod.compile_managed_agent_for_eval(
        definition,
        {"configurable": {"cwd": "/app", "thread_id": "trial-1"}},
    )

    assert len(_FakeFilesystemBackend.instances) == 1
    backend = _FakeFilesystemBackend.instances[0]
    assert backend.root_dir == "/app"
    assert backend.virtual_mode is True
    assert captured["params"]["backend"] is backend
    # Eval trials never mount durable memory, declared or not.
    assert "memory" not in captured["params"]


def test_eval_compile_mounts_project_skills(captured: dict[str, Any]) -> None:
    definition = DeepAgentDefinition({"model": "openai:gpt-5-mini"})
    runtime_mod.compile_managed_agent_for_eval(
        definition,
        {"configurable": {"cwd": "/app"}},
        has_skills=True,
    )

    # The trial backend is rooted at the workspace the Harbor adapter copied the
    # compiled project into, so this resolves to `/app/skills/`.
    assert captured["params"]["skills"] == [runtime_mod.MANAGED_SKILLS_PATH]
    backend = _FakeFilesystemBackend.instances[0]
    assert backend._resolve_path(runtime_mod.MANAGED_SKILLS_PATH) == "/app/skills/"


def test_eval_compile_mounts_no_skills_by_default(captured: dict[str, Any]) -> None:
    definition = DeepAgentDefinition({"model": "openai:gpt-5-mini"})
    runtime_mod.compile_managed_agent_for_eval(
        definition,
        {"configurable": {"cwd": "/app"}},
    )

    assert "skills" not in captured["params"]


def test_eval_compile_respects_configurable_cwd(captured: dict[str, Any]) -> None:
    definition = DeepAgentDefinition({"model": "openai:gpt-5-mini"})
    runtime_mod.compile_managed_agent_for_eval(
        definition,
        {"configurable": {"cwd": "/workspace"}},
    )

    assert _FakeFilesystemBackend.instances[0].root_dir == "/workspace"


def test_eval_compile_preserves_authored_model_object(captured: dict[str, Any]) -> None:
    authored_model = object()
    definition = DeepAgentDefinition({"model": authored_model})

    runtime_mod.compile_managed_agent_for_eval(
        definition,
        {"configurable": {"cwd": "/app"}},
    )

    assert captured["params"]["model"] is authored_model


def test_eval_compile_applies_explicit_model_override(captured: dict[str, Any]) -> None:
    definition = DeepAgentDefinition({"model": object()})

    runtime_mod.compile_managed_agent_for_eval(
        definition,
        {
            "configurable": {
                "cwd": "/app",
                "model": "anthropic:claude-sonnet-4-5",
            }
        },
    )

    assert captured["params"]["model"] == "anthropic:claude-sonnet-4-5"


def test_dynamic_eval_resolves_before_model_override(captured: dict[str, Any]) -> None:
    contexts: list[object] = []

    def build(context: ServerRuntime[object]) -> DeepAgentDefinition:
        contexts.append(
            context.execution_runtime.context if context.execution_runtime is not None else {}
        )
        return define_deep_agent(
            name="selected",
            model="openai:gpt-5-mini",
        )

    asyncio.run(
        runtime_mod.compile_agent_factory_for_eval_async(
            build,
            {
                "context": {"tier": "fast"},
                "configurable": {
                    "cwd": "/app",
                    "model": "anthropic:claude-sonnet-4-5",
                },
            },
        )
    )

    assert contexts == [{"tier": "fast"}]
    assert captured["params"]["model"] == "anthropic:claude-sonnet-4-5"
    assert "memory" not in captured["params"]


def test_dynamic_eval_loads_connector_tools_without_context(captured: dict[str, Any]) -> None:
    contexts: list[object] = []
    connector_tool = object()
    tool_contexts: list[object] = []

    class _ToolsConnector(Connector):
        kind = "test"

        def tools(self, context: object) -> list[object]:
            tool_contexts.append(context)
            return [connector_tool]

    def build(context: ServerRuntime[object]) -> DeepAgentDefinition:
        contexts.append(
            context.execution_runtime.context if context.execution_runtime is not None else {}
        )
        return define_deep_agent(name="dynamic-eval")

    asyncio.run(
        runtime_mod.compile_agent_factory_for_eval_async(
            build,
            {"configurable": {"cwd": "/app", "thread_id": "trial-1"}},
            connectors=[_ToolsConnector()],
        )
    )

    assert contexts == [{}]
    assert tool_contexts == [{}]
    assert captured["params"]["tools"] == [connector_tool]


def test_dynamic_eval_uses_selected_mcp(captured: dict[str, Any]) -> None:
    calls: list[str] = []

    class _TestMcp(McpServersDefinition):
        def __init__(self, tool: str) -> None:
            super().__init__(servers={})
            self.tool = tool

        def tools(self, _context: object = None) -> list[str]:
            calls.append(self.tool)
            return [self.tool]

    discovered = _TestMcp("discovered")
    selected = _TestMcp("selected")

    def build(_context: dict[str, object]) -> DeepAgentDefinition:
        return define_deep_agent(name="dynamic-eval", mcp=[selected])

    asyncio.run(
        runtime_mod.compile_agent_factory_for_eval_async(
            build,
            {"configurable": {"cwd": "/app", "thread_id": "trial-1"}},
            connectors=[discovered],
        )
    )

    assert calls == ["selected"]
    assert captured["params"]["tools"] == ["selected"]
    assert "mcp" not in captured["params"]


def test_dynamic_eval_ignores_selected_sandbox(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime_mod,
        "_managed_sandbox_backend",
        lambda *_args, **_kwargs: pytest.fail("eval must not create a managed sandbox"),
    )

    def build(_context: dict[str, object]) -> DeepAgentDefinition:
        return define_deep_agent(
            name="dynamic-eval",
            sandbox=define_sandbox(default_timeout=600),
        )

    asyncio.run(
        runtime_mod.compile_agent_factory_for_eval_async(
            build,
            {"configurable": {"cwd": "/app", "thread_id": "trial-1"}},
        )
    )

    assert "sandbox" not in captured["params"]
    assert _FakeFilesystemBackend.instances[0].root_dir == "/app"
