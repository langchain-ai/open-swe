"""Unit tests for Context Hub instructions loading."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import ReadResult
from langchain.tools import ToolRuntime
from langgraph.runtime import Runtime

from managed_deepagents import define_deep_agent, define_sandbox, runtime
from managed_deepagents._connector import Connector
from managed_deepagents._connectors.mcp import McpServersDefinition
from managed_deepagents._memory import ManagedCompositeBackend
from managed_deepagents.define_deep_agent import DeepAgentDefinition
from managed_deepagents.runtime import (
    MANAGED_DEEP_AGENT_INSTRUCTIONS,
    _read_context_instructions,
    compile_managed_agent,
)


def _tool_runtime(configurable: dict[str, Any] | None = None) -> ToolRuntime:
    return ToolRuntime(
        state={},
        context=None,
        config={"configurable": configurable or {}},
        stream_writer=lambda _: None,
        tool_call_id=None,
        store=None,
    )


class _ContentBackend:
    """Download fails, so instructions come from a native ``ReadResult``."""

    def __init__(self, *, content: str | None = None, error: str | None = None) -> None:
        self.content = content
        self.error = error
        self.read_calls: list[tuple[str, int, int]] = []
        self.download_calls: list[list[str]] = []

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        self.read_calls.append((file_path, offset, limit))
        if self.error:
            return ReadResult(error=self.error)
        if self.content is None:
            return ReadResult(error="file_not_found")
        return ReadResult(file_data={"content": self.content, "encoding": "utf-8"})

    def download_files(self, paths: list[str]) -> list[Any]:
        self.download_calls.append(list(paths))
        return [SimpleNamespace(path=path, error="file_not_found", content=None) for path in paths]


class _DownloadBackend:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.read_calls: list[str] = []
        self.download_calls: list[list[str]] = []

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        self.read_calls.append(file_path)
        return SimpleNamespace(error=None, content="should not use read")

    def download_files(self, paths: list[str]) -> list[Any]:
        self.download_calls.append(list(paths))
        return [SimpleNamespace(path=paths[0], error=None, content=self.content)]


def test_read_context_instructions_falls_back_to_read_file_data() -> None:
    # Regression: the ``read`` fallback used to look for ``ReadResult.content``,
    # which deepagents never sets, so a failed download dropped the prompt.
    backend = _ContentBackend(content="You are helpful.\n")
    assert _read_context_instructions(backend) == "You are helpful.\n"
    assert backend.download_calls == [["/instructions.md"]]
    assert backend.read_calls[0][0] == "/instructions.md"
    assert backend.read_calls[0][2] > 2000


def test_read_context_instructions_fallback_with_native_backend(tmp_path: Any) -> None:
    class _NoDownloadBackend(FilesystemBackend):
        def download_files(self, paths: list[str]) -> list[Any]:
            return [SimpleNamespace(path=path, error="permission_denied") for path in paths]

    (tmp_path / "instructions.md").write_text("Line one.\nLine two.\n")
    backend = _NoDownloadBackend(root_dir=tmp_path, virtual_mode=True)
    assert _read_context_instructions(backend) == "Line one.\nLine two.\n"


def test_read_context_instructions_prefers_download_files() -> None:
    backend = _DownloadBackend(b"Downloaded prompt\n")
    assert _read_context_instructions(backend) == "Downloaded prompt\n"
    assert backend.download_calls == [["/instructions.md"]]
    assert backend.read_calls == []


def test_read_context_instructions_returns_none_on_error() -> None:
    backend = _ContentBackend(error="File '/instructions.md' not found")
    assert _read_context_instructions(backend) is None


def test_compile_managed_agent_sets_system_prompt_from_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: deploy path must surface Context Hub instructions as system_prompt."""
    captured: dict[str, Any] = {}

    class FakeContextHubBackend:
        instances: ClassVar[list[FakeContextHubBackend]] = []

        def __init__(self, repo: str) -> None:
            self.repo = repo
            self.download_calls: list[list[str]] = []
            FakeContextHubBackend.instances.append(self)

        def download_files(self, paths: list[str]) -> list[Any]:
            self.download_calls.append(list(paths))
            if paths == ["/instructions.md"]:
                return [
                    SimpleNamespace(
                        path=paths[0],
                        error=None,
                        content=b"You are an expert LangChain agent.\n",
                    )
                ]
            return [
                SimpleNamespace(path=path, error="file_not_found", content=None) for path in paths
            ]

        def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
            raise AssertionError("download_files should satisfy instructions loading")

    class FakeAgent:
        def with_config(self, config: dict[str, Any]) -> FakeAgent:
            return self

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )
    monkeypatch.setattr(runtime, "ContextHubBackend", FakeContextHubBackend)
    monkeypatch.setattr(runtime, "StateBackend", lambda: "state-backend")
    monkeypatch.setattr(
        runtime,
        "FilesystemPermission",
        lambda **kwargs: kwargs,
    )

    FakeContextHubBackend.instances.clear()
    definition = DeepAgentDefinition({"model": "test-model"})
    result = compile_managed_agent(definition, context_repo="chat-langchain-on-mda-deploy")

    assert isinstance(result, FakeAgent)
    assert FakeContextHubBackend.instances[0].repo == "chat-langchain-on-mda-deploy"
    assert FakeContextHubBackend.instances[0].download_calls[0] == ["/instructions.md"]
    assert "You are an expert LangChain agent.\n" in captured["system_prompt"]
    assert "memory" not in captured
    assert isinstance(captured["backend"], ManagedCompositeBackend)
    assert "/instructions.md/" in captured["backend"].routes


@pytest.mark.parametrize(
    ("context_repo", "instructions"),
    [
        ("my-agent", "Project instructions.\n"),
        ("my-agent", None),
        (None, "Project instructions.\n"),
        (None, None),
    ],
    ids=["context-hub", "missing-context-hub", "embedded", "no-instructions"],
)
def test_compile_managed_agent_includes_base_prompt(
    monkeypatch: pytest.MonkeyPatch,
    context_repo: str | None,
    instructions: str | None,
) -> None:
    captured: dict[str, Any] = {}

    class FakeAgent:
        def with_config(self, config: dict[str, Any]) -> FakeAgent:
            return self

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )
    monkeypatch.setattr(
        runtime, "ContextHubBackend", lambda _repo: _ContentBackend(content=instructions)
    )
    monkeypatch.setattr(runtime, "CompositeBackend", lambda default, routes: "composite")
    monkeypatch.setattr(runtime, "StateBackend", lambda: "state-backend")
    monkeypatch.setattr(runtime, "FilesystemPermission", lambda **kwargs: kwargs)

    definition = DeepAgentDefinition({"model": "test-model"})
    compile_managed_agent(
        definition,
        context_repo=context_repo,
        system_prompt=instructions if context_repo is None else None,
    )

    assert captured["system_prompt"] == MANAGED_DEEP_AGENT_INSTRUCTIONS + (
        f"\n\n{instructions}" if instructions else ""
    )


@pytest.mark.parametrize(
    ("authored", "expected"),
    [
        (None, MANAGED_DEEP_AGENT_INSTRUCTIONS),
        ("", MANAGED_DEEP_AGENT_INSTRUCTIONS),
        (
            "Dynamic instructions.",
            f"{MANAGED_DEEP_AGENT_INSTRUCTIONS}\n\nDynamic instructions.",
        ),
    ],
    ids=["disabled", "empty", "provided"],
)
def test_dynamic_instructions_replace_context_hub_prompt(
    monkeypatch: pytest.MonkeyPatch,
    authored: str | None,
    expected: str,
) -> None:
    captured: dict[str, Any] = {}
    backend = _ContentBackend(content="Static instructions.\n")

    class FakeAgent:
        def with_config(self, config: dict[str, Any]) -> FakeAgent:
            return self

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )
    monkeypatch.setattr(runtime, "ContextHubBackend", lambda _repo: backend)
    monkeypatch.setattr(runtime, "CompositeBackend", lambda default, routes: "composite")
    monkeypatch.setattr(runtime, "StateBackend", lambda: "state-backend")
    monkeypatch.setattr(runtime, "FilesystemPermission", lambda **kwargs: kwargs)

    definition = DeepAgentDefinition(
        {
            "model": "test-model",
            "instructions": authored,
            "skills": None,
        }
    )
    compile_managed_agent(definition, context_repo="my-agent", has_skills=True)

    assert captured["system_prompt"] == expected
    assert backend.download_calls == []
    assert all(field not in captured for field in ("instructions", "skills", "memory"))


@pytest.mark.parametrize(
    ("selection", "expected_tools"),
    [
        ("omitted", ["other", "discovered"]),
        ("empty", ["other"]),
        ("disabled", ["other"]),
        ("selected", ["other", "selected"]),
    ],
)
def test_dynamic_mcp_selection_preserves_other_connectors(
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
    expected_tools: list[str],
) -> None:
    captured: dict[str, Any] = {}
    calls: list[str] = []

    class FakeAgent:
        def with_config(self, config: dict[str, Any]) -> FakeAgent:
            return self

    class ToolConnector(Connector):
        kind = "test"

        def tools(self, _context: object) -> list[str]:
            calls.append("other")
            return ["other"]

    class TestMcp(McpServersDefinition):
        def __init__(self, tool: str) -> None:
            super().__init__(servers={})
            self.tool = tool

        def tools(self, _context: object = None) -> list[str]:
            calls.append(self.tool)
            return [self.tool]

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )
    discovered = TestMcp("discovered")
    selected = TestMcp("selected")

    def build(_context: object) -> DeepAgentDefinition:
        if selection == "omitted":
            return define_deep_agent(name="dynamic-mcp")
        return define_deep_agent(
            name="dynamic-mcp",
            mcp=[] if selection == "empty" else None if selection == "disabled" else [selected],
        )

    compile_managed_agent(
        build,
        connectors=[ToolConnector(), discovered],
        report_telemetry=False,
    )

    assert calls == expected_tools
    assert captured["tools"] == expected_tools
    assert "mcp" not in captured


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ("omitted", "discovered"),
        ("selected", "selected"),
        ("disabled", None),
    ],
)
def test_dynamic_sandbox_selection(
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
    expected: str | None,
) -> None:
    captured: dict[str, Any] = {}
    selected_sandboxes: list[object] = []
    selections: list[object] = []
    discovered = define_sandbox(default_timeout=100)
    selected = define_sandbox(default_timeout=200)

    class FakeAgent:
        def with_config(self, _config: dict[str, Any]) -> FakeAgent:
            return self

    class FakeBackend:
        def bind_runtime(self, _runtime: object) -> object:
            return object()

    def managed_sandbox(sandbox: object, *_args: object, **_kwargs: object) -> FakeBackend:
        selected_sandboxes.append(sandbox)
        selections.append(_kwargs.get("selection"))
        return FakeBackend()

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )
    monkeypatch.setattr(runtime, "_managed_sandbox_backend", managed_sandbox)
    monkeypatch.setattr(runtime, "_append_channel_response_format_middleware", lambda _merged: None)

    def build(_context: object) -> DeepAgentDefinition:
        if selection == "omitted":
            return define_deep_agent(name="dynamic-sandbox")
        return define_deep_agent(
            name="dynamic-sandbox",
            sandbox=None if selection == "disabled" else selected,
        )

    compile_managed_agent(
        build,
        sandbox=discovered,
        report_telemetry=False,
    )

    assert "sandbox" not in captured
    expected_sandbox = {"discovered": discovered, "selected": selected}.get(expected)
    assert selected_sandboxes == ([] if expected_sandbox is None else [expected_sandbox])
    if expected_sandbox is None:
        assert selections == []
    elif selection == "selected":
        assert selections == [runtime._sandbox_selection(selected)]
    else:
        assert selections == [None]


def _compile_with_setup_recipe(
    monkeypatch: pytest.MonkeyPatch,
    selected: object,
    *,
    declared: object = None,
    sandbox_setup: bool = True,
    is_run: bool = True,
) -> list[tuple[object, object]]:
    """Compile a factory selecting ``selected`` and return the sandbox it provisions."""
    monkeypatch.setenv(runtime.RECIPE_SNAPSHOT_ENV, "agent--setup-abc")
    provisioned: list[tuple[object, object]] = []

    class FakeAgent:
        def with_config(self, _config: dict[str, Any]) -> FakeAgent:
            return self

    class FakeBackend:
        def bind_runtime(self, _runtime: object) -> object:
            return object()

    def managed_sandbox(sandbox: object, *_args: object, **kwargs: object) -> FakeBackend:
        provisioned.append((sandbox, kwargs.get("selection")))
        return FakeBackend()

    monkeypatch.setattr(runtime, "create_deep_agent", lambda **_kwargs: FakeAgent())
    monkeypatch.setattr(runtime, "_managed_sandbox_backend", managed_sandbox)
    monkeypatch.setattr(runtime, "_append_channel_response_format_middleware", lambda _merged: None)

    compile_managed_agent(
        lambda _runtime: define_deep_agent(name="dynamic-sandbox", sandbox=selected),
        sandbox=declared,
        sandbox_setup=sandbox_setup,
        is_run=is_run,
        report_telemetry=False,
    )
    return provisioned


@pytest.mark.parametrize(
    "selected",
    [
        define_sandbox(vcpus=4),
        define_sandbox(docker_image="python:3.12-slim"),
    ],
    ids=["no base", "declared base"],
)
def test_setup_recipe_selection_clones_the_recipe(
    monkeypatch: pytest.MonkeyPatch,
    selected: object,
) -> None:
    declared = define_sandbox(docker_image="python:3.12-slim")

    [(sandbox, selection)] = _compile_with_setup_recipe(monkeypatch, selected, declared=declared)

    assert "docker_image" not in sandbox.options
    assert runtime._sandbox_create_kwargs(sandbox, selection)["snapshot_name"] == (
        "agent--setup-abc"
    )
    # The selection keeps its identity, so live boxes are still found.
    assert selection == runtime._sandbox_selection(selected)


def test_setup_recipe_rejects_a_base_the_recipe_was_not_baked_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError) as error:
        _compile_with_setup_recipe(
            monkeypatch,
            define_sandbox(docker_image="node:22"),
            declared=define_sandbox(docker_image="python:3.12-slim"),
        )

    assert str(error.value).startswith(
        'sandbox/setup.sh is baked on docker_image "python:3.12-slim", '
        'but this run selected docker_image "node:22".'
    )


def test_setup_recipe_rejects_any_base_without_a_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match="baked on the default sandbox image"):
        _compile_with_setup_recipe(monkeypatch, define_sandbox(snapshot_name="custom"))


def test_setup_recipe_check_is_skipped_without_setup_or_outside_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = define_sandbox(snapshot_name="custom")

    without_setup = _compile_with_setup_recipe(monkeypatch, selected, sandbox_setup=False)
    inspection = _compile_with_setup_recipe(monkeypatch, selected, is_run=False)

    [(sandbox, selection)] = without_setup
    assert runtime._sandbox_create_kwargs(sandbox, selection)["snapshot_name"] == "custom"
    assert all(sandbox.options.get("snapshot_name") == "custom" for sandbox, _ in inspection)


def test_compile_managed_agent_applies_metadata_via_with_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    with_config_calls: list[dict[str, Any]] = []

    class FakeAgent:
        def with_config(self, config: dict[str, Any]) -> str:
            with_config_calls.append(config)
            return "agent-with-metadata"

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )
    monkeypatch.setattr(runtime, "ContextHubBackend", lambda repo: None)
    monkeypatch.setattr(runtime, "CompositeBackend", lambda default, routes: "composite")
    monkeypatch.setattr(runtime, "StateBackend", lambda: "state-backend")
    monkeypatch.setattr(runtime, "FilesystemPermission", lambda **kwargs: kwargs)

    definition = DeepAgentDefinition(
        {
            "model": "test-model",
            "metadata": {
                "source_type": "Chat-LangChain",
                "prompt_source": "hub:demo",
            },
        }
    )
    result = compile_managed_agent(definition)

    assert result == "agent-with-metadata"
    assert "metadata" not in captured
    assert with_config_calls == [
        {
            "metadata": {
                "source_type": "Chat-LangChain",
                "prompt_source": "hub:demo",
            },
        }
    ]


def test_compile_managed_agent_preserves_upstream_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_config_calls: list[dict[str, Any]] = []

    class FakeAgent:
        def with_config(self, config: dict[str, Any]) -> str:
            with_config_calls.append(config)
            return "unexpected-config-binding"

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **_kwargs: FakeAgent(),
    )
    monkeypatch.setattr(runtime, "ContextHubBackend", lambda repo: None)
    monkeypatch.setattr(runtime, "CompositeBackend", lambda default, routes: "composite")
    monkeypatch.setattr(runtime, "StateBackend", lambda: "state-backend")
    monkeypatch.setattr(runtime, "FilesystemPermission", lambda **kwargs: kwargs)

    definition = DeepAgentDefinition({"model": "test-model"})
    result = compile_managed_agent(definition, report_telemetry=False)

    assert isinstance(result, FakeAgent)
    assert with_config_calls == []


def test_compile_injects_sandbox_backend_without_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    seen: dict[str, Any] = {}

    class FakeAgent:
        def with_config(self, _config: dict[str, Any]) -> FakeAgent:
            return self

    class FakeTool:
        def __init__(self, func: Any) -> None:
            self.func = func
            self.coroutine = None

        def model_copy(self, *, update: dict[str, Any]) -> FakeTool:
            clone = FakeTool(self.func)
            for key, value in update.items():
                setattr(clone, key, value)
            return clone

    class FakeMiddleware:
        def before_model(self, state: Any, runtime: Any) -> None:
            state["runtime"] = runtime

    bound_backend = object()

    class FakeBackend:
        def bind_runtime(self, _runtime: object) -> object:
            return bound_backend

    def tool_func(*, runtime: Any) -> None:
        seen["tool_runtime"] = runtime

    def connector_func(*, runtime: Any) -> None:
        seen["connector_runtime"] = runtime

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )
    monkeypatch.setattr(
        runtime, "_managed_sandbox_backend", lambda *_args, **_kwargs: FakeBackend()
    )
    monkeypatch.setattr(
        runtime, "load_connector_tools", lambda _connectors: [FakeTool(connector_func)]
    )
    monkeypatch.setattr(runtime, "_append_channel_response_format_middleware", lambda _merged: None)

    definition = DeepAgentDefinition(
        {
            "model": "test-model",
            "tools": [FakeTool(tool_func)],
            "middleware": [FakeMiddleware()],
        }
    )
    runtime.compile_managed_agent(
        definition,
        sandbox=SimpleNamespace(kind="sandbox", options={}),
        connectors=[SimpleNamespace(kind="test")],
    )

    captured["tools"][0].func(runtime=_tool_runtime())
    captured["tools"][1].func(runtime=_tool_runtime())
    authored = next(entry for entry in captured["middleware"] if isinstance(entry, FakeMiddleware))
    state: dict[str, Any] = {}
    authored.before_model(state, runtime=Runtime())

    assert seen["tool_runtime"].backend is bound_backend
    assert state["runtime"].backend is bound_backend
    assert not hasattr(seen["connector_runtime"], "backend")


def test_compile_sets_backend_to_none_without_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    seen: dict[str, Any] = {}

    class FakeAgent:
        def with_config(self, _config: dict[str, Any]) -> FakeAgent:
            return self

    class FakeTool:
        def __init__(self, func: Any) -> None:
            self.func = func
            self.coroutine = None

        def model_copy(self, *, update: dict[str, Any]) -> FakeTool:
            clone = FakeTool(self.func)
            for key, value in update.items():
                setattr(clone, key, value)
            return clone

    def tool_func(*, runtime: Any) -> None:
        seen["runtime"] = runtime

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )
    monkeypatch.setattr(runtime, "_append_channel_response_format_middleware", lambda _merged: None)

    definition = DeepAgentDefinition({"model": "test-model", "tools": [FakeTool(tool_func)]})
    runtime.compile_managed_agent(
        definition,
    )

    captured["tools"][0].func(runtime=_tool_runtime({"thread_id": "thread-1"}))

    assert seen["runtime"].backend is None


def test_compile_injects_backend_into_nested_authored_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    seen: dict[str, Any] = {}

    class FakeAgent:
        def with_config(self, _config: dict[str, Any]) -> FakeAgent:
            return self

    class FakeTool:
        def __init__(self, name: str, func: Any) -> None:
            self.name = name
            self.func = func
            self.coroutine = None

        def model_copy(self, *, update: dict[str, Any]) -> FakeTool:
            clone = FakeTool(self.name, self.func)
            for key, value in update.items():
                setattr(clone, key, value)
            return clone

    class FakeMiddleware:
        def __init__(self, tools: list[FakeTool]) -> None:
            self.tools = tools

    def subagent_func(*, runtime: Any) -> None:
        seen["subagent"] = runtime

    def middleware_tool_func(*, runtime: Any) -> None:
        seen["middleware_tool"] = runtime

    monkeypatch.setattr(
        runtime,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or FakeAgent(),
    )
    monkeypatch.setattr(
        runtime,
        "_managed_sandbox_backend",
        lambda *_args, **_kwargs: SimpleNamespace(bind_runtime=lambda _runtime: object()),
    )
    monkeypatch.setattr(runtime, "_append_channel_response_format_middleware", lambda _merged: None)

    definition = DeepAgentDefinition(
        {
            "model": "test-model",
            "middleware": [FakeMiddleware([FakeTool("middleware-tool", middleware_tool_func)])],
            "subagents": [
                {
                    "name": "researcher",
                    "description": "Research",
                    "system_prompt": "Research the request.",
                    "tools": [FakeTool("subagent-tool", subagent_func)],
                }
            ],
        }
    )
    runtime.compile_managed_agent(
        definition,
        sandbox=SimpleNamespace(kind="sandbox", options={}),
    )

    call_runtime = _tool_runtime({"thread_id": "thread-1"})
    captured["subagents"][0]["tools"][0].func(runtime=call_runtime)
    authored = next(entry for entry in captured["middleware"] if isinstance(entry, FakeMiddleware))
    authored.tools[0].func(runtime=call_runtime)

    assert seen["subagent"].backend is not None
    assert seen["middleware_tool"].backend is not None
