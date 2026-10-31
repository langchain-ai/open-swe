"""Test native runtime injection and local structural tool boundaries."""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any, cast

import pytest
from langchain.tools import ToolRuntime
from langgraph.store.memory import InMemoryStore
from pydantic import TypeAdapter, ValidationError

from managed_deepagents import ManagedDeepAgentRuntime, ManagedToolRuntime, runtime
from managed_deepagents._managed_thread import ManagedThread
from managed_deepagents._managed_tools import (
    ManagedRuntimeExtras,
    adapt_managed_runtime,
    managed_runtime_overrides,
    with_managed_runtime,
    wrap_tools_with_managed_runtime,
)
from managed_deepagents._runtime_schedules import RuntimeSchedules

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol


class _Runtime(ToolRuntime):
    """Native tool runtime with short fixture defaults."""

    def __init__(self, configurable: dict[str, Any], **extra: Any) -> None:
        super().__init__(
            state={},
            context=None,
            config={"configurable": configurable},
            stream_writer=lambda _: None,
            tool_call_id=None,
            store=None,
        )
        for key, value in extra.items():
            setattr(self, key, value)


class _CopyTool:
    """Pydantic-like tool: ``model_copy(update=...)`` returns a new instance."""

    def __init__(self, name: str, func: Any = None, coroutine: Any = None) -> None:
        self.name = name
        self.func = func
        self.coroutine = coroutine

    def model_copy(self, *, update: dict[str, Any]) -> _CopyTool:
        clone = _CopyTool(self.name, self.func, self.coroutine)
        for key, value in update.items():
            setattr(clone, key, value)
        return clone


class _MutableTool:
    """Non-pydantic tool: no ``model_copy``, so the seam assigns in place."""

    def __init__(self, name: str, func: Any = None) -> None:
        self.name = name
        self.func = func


class _FlatRuntime:
    """Runtime bag that exposes ``configurable`` without a nested ``config``."""

    def __init__(self, configurable: dict[str, Any], **extra: Any) -> None:
        self.configurable = configurable
        for key, value in extra.items():
            setattr(self, key, value)


class TestManagedRuntimeOverrides:
    def test_builds_server_info_principal_from_trusted_auth_user(self) -> None:
        overrides = managed_runtime_overrides(
            _Runtime({"langgraph_auth_user": {"mda_user_id": "u1", "mda_organization_id": "org9"}})
        )
        assert overrides["server_info"].principal.id == "u1"
        assert overrides["server_info"].user.mda_user_id == "u1"

    def test_builds_server_info_principal_from_flat_configurable(self) -> None:
        overrides = managed_runtime_overrides(
            _FlatRuntime(
                {"langgraph_auth_user": {"mda_user_id": "u1", "mda_organization_id": "org9"}}
            )
        )
        assert overrides["server_info"].principal.id == "u1"

    def test_adds_server_info_principal_and_strips_reserved_config_keys(self) -> None:
        runtime = _Runtime(
            {
                "thread_id": "t1",
                "user_id": "spoof",
                "mda_organization_id": "spoof",
                "langgraph_auth_user": {"mda_user_id": "u1"},
            }
        )
        overrides = managed_runtime_overrides(runtime)
        assert overrides["server_info"].principal.id == "u1"
        assert overrides["config"]["configurable"] == {
            "thread_id": "t1",
            "langgraph_auth_user": {"mda_user_id": "u1"},
        }

    def test_no_config_override_without_reserved_keys(self) -> None:
        overrides = managed_runtime_overrides(
            _Runtime({"thread_id": "t1", "langgraph_auth_user": {"mda_user_id": "u1"}})
        )
        assert overrides["server_info"].principal.id == "u1"
        assert "config" not in overrides

    def test_managed_optional_fields_are_always_supplied(self) -> None:
        for native, thread_id in ((_Runtime({"thread_id": "t1"}), "t1"), (object(), "")):
            overrides = managed_runtime_overrides(native)
            schedules = overrides.pop("schedules")
            assert overrides == {
                "thread": ManagedThread(id=thread_id, origin=None),
                "channel": None,
                "backend": None,
            }
            assert isinstance(schedules, RuntimeSchedules)
            assert schedules._runtime is native

    def test_adds_server_info_principal_and_strips_reserved_keys_from_flat_configurable(
        self,
    ) -> None:
        runtime = _FlatRuntime(
            {
                "thread_id": "t1",
                "user_id": "spoof",
                "langgraph_auth_user": {"mda_user_id": "u1"},
            }
        )
        overrides = managed_runtime_overrides(runtime)
        assert overrides["server_info"].principal.id == "u1"
        assert overrides["configurable"] == {
            "thread_id": "t1",
            "langgraph_auth_user": {"mda_user_id": "u1"},
        }

    def test_injects_server_info_principal_from_native_config_at_call_time(self) -> None:
        seen: dict[str, Any] = {}

        def func(*, runtime: Any) -> str:
            seen["runtime"] = runtime
            return runtime.server_info.principal.id

        wrapped = with_managed_runtime(_CopyTool("probe", func=func))
        wrapped.func(runtime=_Runtime({"langgraph_auth_user": {"mda_user_id": "u1"}}))
        assert seen["runtime"].server_info.principal.id == "u1"
        assert seen["runtime"].backend is None
        assert "user_id" not in seen["runtime"].config["configurable"]


class TestManagedRuntime:
    @pytest.mark.parametrize(
        "runtime_type",
        [ManagedToolRuntime, ManagedToolRuntime[dict[str, object], dict[str, object]]],
    )
    def test_schema_accepts_injected_native_runtime_without_traversing_fields(
        self, runtime_type: object
    ) -> None:
        adapter = TypeAdapter(runtime_type)
        native = _Runtime({})
        assert adapter.validate_python(native) is native
        with pytest.raises(ValidationError, match="ToolRuntime"):
            adapter.validate_python(object())

    def test_exposes_typed_extras_and_delegates_other_fields(self) -> None:
        underlying = _Runtime(
            {"thread_id": "t1", "langgraph_auth_user": {"mda_user_id": "u1"}},
            state={"messages": []},
            store=InMemoryStore(),
        )
        extras: ManagedRuntimeExtras = managed_runtime_overrides(underlying)
        runtime = adapt_managed_runtime(underlying, extras)

        assert runtime.server_info is not None
        assert runtime.server_info.principal.id == "u1"
        assert runtime.state == {"messages": []}
        assert runtime.store is underlying.store
        assert runtime.config["configurable"]["thread_id"] == "t1"

    def test_is_exported_from_package(self) -> None:
        assert ManagedDeepAgentRuntime is not None
        assert ManagedRuntimeExtras is not None
        assert not hasattr(__import__("managed_deepagents"), "ManagedRuntimeExtras")

    def test_annotation_is_langchain_tool_injectable(self) -> None:
        """``@tool`` must treat ManagedDeepAgentRuntime like ToolRuntime."""
        langchain_core_tools = pytest.importorskip("langchain_core.tools.base")
        assert langchain_core_tools._is_directly_injected_arg_type(ManagedDeepAgentRuntime)


class TestWithManagedRuntime:
    def test_injects_sandbox_backend_without_identity(self) -> None:
        seen: dict[str, Any] = {}
        backend = cast("BackendProtocol", object())

        def func(*, runtime: Any) -> None:
            seen["runtime"] = runtime

        wrapped = with_managed_runtime(_CopyTool("probe", func=func), lambda _runtime: backend)
        wrapped.func(runtime=_Runtime({"thread_id": "thread-1"}))

        assert seen["runtime"].backend is backend
        assert seen["runtime"].server_info is None

    def test_injects_server_info_principal_into_runtime_the_tool_receives(self) -> None:
        seen: dict[str, Any] = {}

        def func(*, city: str, runtime: Any) -> str:
            seen["runtime"] = runtime
            return f"weather in {city} for {runtime.server_info.principal.id}"

        wrapped = with_managed_runtime(_CopyTool("weather", func=func))
        runtime = _Runtime(
            {"thread_id": "t1", "langgraph_auth_user": {"mda_user_id": "u1"}},
            state={"messages": []},
        )
        result = wrapped.func(city="SF", runtime=runtime)

        assert result == "weather in SF for u1"
        assert isinstance(seen["runtime"], ManagedToolRuntime)
        assert seen["runtime"].server_info.principal.id == "u1"
        assert seen["runtime"].server_info.user.identity == "u1"
        assert seen["runtime"].state == {"messages": []}
        assert seen["runtime"].config["configurable"]["thread_id"] == "t1"
        # The tool identity/schema is untouched.
        assert wrapped.name == "weather"

    def test_adapts_native_runtime_without_auth_user(self) -> None:
        seen: dict[str, Any] = {}

        def func(*, runtime: Any) -> str:
            seen["runtime"] = runtime
            return "ok"

        wrapped = with_managed_runtime(_CopyTool("probe", func=func))
        runtime = _Runtime({"thread_id": "t1"})
        wrapped.func(runtime=runtime)

        assert isinstance(seen["runtime"], ManagedToolRuntime)
        assert seen["runtime"].config is runtime.config
        assert not hasattr(seen["runtime"], "server_info") or seen["runtime"].server_info is None
        assert not hasattr(seen["runtime"], "identity")
        assert seen["runtime"].backend is None

    def test_strips_spoofable_reserved_keys_from_config(self) -> None:
        seen: dict[str, Any] = {}

        def func(*, runtime: Any) -> str:
            seen["runtime"] = runtime
            return "ok"

        wrapped = with_managed_runtime(_CopyTool("probe", func=func))
        wrapped.func(
            runtime=_Runtime(
                {
                    "thread_id": "t1",
                    "user_id": "spoof",
                    "langgraph_auth_user": {"mda_user_id": "u1"},
                }
            )
        )

        runtime = seen["runtime"]
        assert runtime.server_info.principal.id == "u1"
        assert "user_id" not in runtime.config["configurable"]
        assert runtime.config["configurable"]["thread_id"] == "t1"

    def test_wraps_coroutine(self) -> None:
        seen: dict[str, Any] = {}

        async def coro(*, runtime: Any) -> str:
            seen["runtime"] = runtime
            return runtime.server_info.principal.id

        wrapped = with_managed_runtime(_CopyTool("async_probe", coroutine=coro))
        runtime = _Runtime({"langgraph_auth_user": {"mda_user_id": "u9"}})

        assert asyncio.run(wrapped.coroutine(runtime=runtime)) == "u9"
        assert seen["runtime"].server_info.principal.id == "u9"

    def test_preserves_signature_for_runtime_detection(self) -> None:
        def func(*, city: str, runtime: Any) -> str:
            return city

        wrapped = with_managed_runtime(_CopyTool("weather", func=func))
        params = inspect.signature(wrapped.func).parameters
        assert "runtime" in params
        assert "city" in params

    def test_falls_back_to_in_place_assignment(self) -> None:
        def func(*, runtime: Any) -> str:
            return runtime.server_info.principal.id

        tool = _MutableTool("probe", func=func)
        wrapped = with_managed_runtime(tool)

        assert wrapped is tool  # mutated in place, not copied
        runtime = _Runtime({"langgraph_auth_user": {"mda_user_id": "u1"}})
        assert wrapped.func(runtime=runtime) == "u1"

    def test_tool_without_callables_passes_through(self) -> None:
        tool = _CopyTool("noop")
        assert with_managed_runtime(tool) is tool


class TestWrapToolsWithManagedRuntime:
    @pytest.mark.parametrize("is_async", [False, True])
    @pytest.mark.parametrize("has_sandbox", [False, True])
    @pytest.mark.parametrize("decorated", [False, True])
    def test_functions_receive_backend_through_tool_node(
        self, is_async: bool, has_sandbox: bool, decorated: bool
    ) -> None:
        graph = pytest.importorskip("langgraph.graph")
        prebuilt = pytest.importorskip("langgraph.prebuilt")
        messages = pytest.importorskip("langchain_core.messages")
        backend = cast("BackendProtocol", object()) if has_sandbox else None
        seen: list[ManagedDeepAgentRuntime] = []

        def read_file(path: str, runtime: ManagedDeepAgentRuntime) -> str:
            """Read an image from the sandbox."""
            seen.append(runtime)
            assert runtime.backend is backend
            return path

        async def aread_file(path: str, runtime: ManagedDeepAgentRuntime) -> str:
            """Read an image from the sandbox asynchronously."""
            return read_file(path, runtime)

        original = aread_file if is_async else read_file
        create_tool = pytest.importorskip("langchain_core.tools").tool
        authored_tool = create_tool(original) if decorated else original
        tools = wrap_tools_with_managed_runtime([authored_tool], lambda _runtime: backend)
        node = prebuilt.ToolNode(tools, handle_tool_errors=False)
        tool = node.tools_by_name[original.__name__]
        assert set(tool.tool_call_schema.model_json_schema()["properties"]) == {"path"}
        assert tool.description == original.__doc__
        builder = graph.StateGraph(graph.MessagesState)
        builder.add_node("tools", node)
        builder.add_edge(graph.START, "tools")
        builder.add_edge("tools", graph.END)
        agent = builder.compile()
        state = {
            "messages": [
                messages.AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": original.__name__,
                            "args": {"path": "/workspace/image.png"},
                            "id": "read",
                        }
                    ],
                )
            ]
        }
        config = {"configurable": {"thread_id": "file-tool"}}
        result = (
            asyncio.run(agent.ainvoke(state, config)) if is_async else agent.invoke(state, config)
        )
        assert result["messages"][-1].content == "/workspace/image.png"
        assert len(seen) == 1
        assert seen[0].config["configurable"]["thread_id"] == "file-tool"
        assert isinstance(seen[0], ManagedToolRuntime)
        assert isinstance(seen[0], ToolRuntime)
        seen[0].emit_output_delta({"delta": "supported"})

    def test_wraps_every_tool(self) -> None:
        seen: list[Any] = []

        def make(index: int) -> Any:
            def func(*, runtime: Any) -> int:
                seen.append(runtime.server_info.principal.id)
                return index

            return _CopyTool(f"t{index}", func=func)

        wrapped = wrap_tools_with_managed_runtime([make(1), make(2)])
        runtime = _Runtime({"langgraph_auth_user": {"mda_user_id": "u1"}})
        for tool in wrapped:
            tool.func(runtime=runtime)

        assert seen == ["u1", "u1"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))


def test_runtime_channel_is_none_without_a_current_delivery() -> None:
    assert adapt_managed_runtime(_Runtime({}), {}).channel is None


# Which authored subagents get the managed runtime wrapping.


@pytest.fixture
def wrapped_tools(monkeypatch: pytest.MonkeyPatch) -> list[list[Any]]:
    calls: list[list[Any]] = []

    def fake_wrap(tools: list[Any], _backend_factory: Any) -> list[Any]:
        calls.append(tools)
        return [("wrapped", tool) for tool in tools]

    monkeypatch.setattr(runtime, "wrap_tools_with_managed_runtime", fake_wrap)
    return calls


def _wrap(spec: dict[str, Any]) -> Any:
    return runtime._wrap_declarative_subagents([spec], backend_factory=object())[0]


@pytest.mark.parametrize(
    "spec",
    [
        {"name": "remote", "description": "d", "graph_id": "research", "tools": ["t"]},
        {"name": "compiled", "description": "d", "runnable": object()},
    ],
    ids=["remote-graph", "compiled-runnable"],
)
def test_subagents_built_elsewhere_are_left_alone(
    spec: dict[str, Any], wrapped_tools: list[list[Any]]
) -> None:
    assert _wrap(spec) is spec
    assert wrapped_tools == []
