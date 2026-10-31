"""Test middleware adaptation with native tool and lifecycle runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.runtime import Runtime

from managed_deepagents import ManagedRuntime, ManagedToolRuntime, _managed_middleware
from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._managed_middleware import (
    _ManagedMiddlewareBinding,
    managed_middleware_overrides,
    with_managed_middleware,
    wrap_middleware_with_managed_runtime,
)
from managed_deepagents._managed_thread import no_thread
from managed_deepagents._runtime_schedules import RuntimeSchedules

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol


def _runtime(configurable: dict[str, Any] | None = None, **extra: Any) -> Runtime | ToolRuntime:
    if configurable is None:
        return Runtime(**extra)
    return ToolRuntime(
        state={},
        context=extra.pop("context", None),
        config={"configurable": configurable},
        stream_writer=lambda _: None,
        tool_call_id=None,
        store=None,
        **extra,
    )


_AUTH = {"langgraph_auth_user": {"mda_user_id": "u1", "mda_organization_id": "org9"}}


class TestManagedMiddlewareOverrides:
    def test_builds_identity_from_runtime_config(self) -> None:
        overrides = managed_middleware_overrides(_runtime(_AUTH))
        assert overrides["server_info"].user.mda_user_id == "u1"

    def test_empty_without_resolvable_actor(self) -> None:
        native = _runtime({})
        overrides = managed_middleware_overrides(native)
        schedules = overrides.pop("schedules")
        assert overrides == {"thread": no_thread(), "channel": None, "backend": None}
        assert isinstance(schedules, RuntimeSchedules)
        assert schedules._runtime is native

    def test_falls_back_to_ambient_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_managed_middleware, "_ambient_config", lambda: {"configurable": _AUTH})
        # Runtime without ``config`` (lifecycle Runtime) still resolves identity.
        overrides = managed_middleware_overrides(_runtime())
        assert overrides["server_info"].user.mda_user_id == "u1"

    def test_resolves_channel_from_current_context(self, monkeypatch: pytest.MonkeyPatch) -> None:

        register_channel_messaging([SimpleNamespace(name="slack", kind="slack")])
        configurable = {
            "langgraph_auth_user": {
                "mda_user_id": "u1",
                "mda_organization_id": "org9",
                "mda_source_provider": "slack",
                "mda_source_thread_id": "source-thread",
            },
            "mda_channel_binding": {
                "name": "slack",
                "provider": "slack",
                "source_thread_key": "source-thread",
                "event": {"type": "message", "messages": []},
                "target": {"channel": "C1"},
                "transport": "direct",
                "delivery_id": "d1",
            },
        }
        context = {"mda_channel": configurable.pop("mda_channel_binding")}
        overrides = managed_middleware_overrides(_runtime(configurable, context=context))
        assert overrides["channel"] is not None
        assert overrides["channel"].name == "slack"
        assert overrides["channel"].provider == "slack"
        assert "mda_channel_binding" not in configurable


class TestManagedMiddlewareBinding:
    def test_wraps_tools_from_getter_only_property(self) -> None:
        seen: dict[str, Any] = {}
        backend = cast("BackendProtocol", object())

        class Tool:
            def __init__(self, func: Any) -> None:
                self.func = func
                self.coroutine = None

            def model_copy(self, *, update: dict[str, Any]) -> Tool:
                clone = Tool(self.func)
                for key, value in update.items():
                    setattr(clone, key, value)
                return clone

        def tool_func(*, runtime: Any) -> None:
            seen["runtime"] = runtime

        tool = Tool(tool_func)

        class MW:
            @property
            def tools(self) -> tuple[Tool, ...]:
                return (tool,)

            def before_model(self, state: Any, runtime: Any) -> None:
                state["runtime"] = runtime

        wrapped = with_managed_middleware(MW(), lambda _runtime: backend)
        wrapped_tool = wrapped.tools[0]
        wrapped_tool.func(runtime=_runtime({"thread_id": "thread-1"}))
        state: dict[str, Any] = {}
        wrapped.before_model(state, runtime=_runtime({"thread_id": "thread-1"}))

        assert wrapped_tool is not tool
        assert seen["runtime"].backend is backend
        assert isinstance(wrapped, MW)
        assert type(wrapped).before_model is MW.before_model
        assert state["runtime"].backend is backend

    def test_shallow_copy_preserves_author_class(self) -> None:
        class MW:
            def before_model(self, state: Any, runtime: Any) -> None: ...

        original = MW()
        clone = _ManagedMiddlewareBinding().bind(original)

        assert clone is not original
        assert clone.__class__ is original.__class__
        assert type(clone) is MW

    def test_public_entry_uses_binding(self) -> None:
        class MW:
            def before_model(self, state: Any, runtime: Any) -> None: ...

        original = MW()
        clone = with_managed_middleware(original)
        assert clone is not original
        assert clone.__class__ is MW


class TestWithManagedMiddlewareLifecycle:
    def test_scopes_backend_from_ambient_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        configurable = {"thread_id": "ambient-thread", "user_id": "spoofed"}
        monkeypatch.setattr(
            _managed_middleware,
            "_ambient_config",
            lambda: {"configurable": configurable},
        )
        writes: list[tuple[str, str, str]] = []

        class Backend:
            def __init__(self, thread_id: str) -> None:
                self.thread_id = thread_id

            def read(self, path: str) -> object:
                return SimpleNamespace(content=f"{self.thread_id}:{path}")

            def write(self, path: str, content: str) -> None:
                writes.append((path, content, self.thread_id))

        def backend_factory(runtime: object) -> Backend:
            assert isinstance(runtime, Mapping)
            scoped = runtime["configurable"]
            assert isinstance(scoped, Mapping)
            return Backend(cast("str", scoped["thread_id"]))

        class MW:
            def before_model(self, state: Any, runtime: ManagedRuntime) -> None:
                assert runtime.backend is not None
                state["result"] = runtime.backend.read("/input.txt")
                runtime.backend.write("/output.txt", "result")
                state["runtime"] = runtime

        clone = with_managed_middleware(MW(), backend_factory)
        state: dict[str, Any] = {}
        clone.before_model(state, runtime=_runtime())

        assert state["result"].content == "ambient-thread:/input.txt"
        assert writes == [("/output.txt", "result", "ambient-thread")]
        assert not hasattr(state["runtime"], "configurable")
        assert not hasattr(state["runtime"], "config")

    def test_injects_sandbox_backend_without_identity(self) -> None:
        backend = cast("BackendProtocol", object())

        class MW:
            def before_model(self, state: Any, runtime: ManagedRuntime) -> None:
                state["seen"] = runtime

        clone = with_managed_middleware(MW(), lambda _runtime: backend)
        state: dict[str, Any] = {}
        clone.before_model(state, runtime=_runtime({"thread_id": "thread-1"}))

        assert state["seen"].backend is backend
        assert state["seen"].server_info is None

    def test_injects_identity_into_before_model_runtime(self) -> None:
        class MW:
            def before_model(self, state: Any, runtime: ManagedRuntime) -> None:
                state["seen"] = runtime

        original = MW()
        clone = with_managed_middleware(original)
        assert clone is not original  # a shallow copy is wrapped, not the original
        assert clone.__class__ is original.__class__

        state: dict[str, Any] = {}
        clone.before_model(state, runtime=_runtime(_AUTH))
        assert isinstance(state["seen"], ManagedToolRuntime)
        assert state["seen"].server_info.principal.id == "u1"
        assert state["seen"].config["configurable"]["langgraph_auth_user"]["mda_user_id"] == "u1"

    def test_only_overridden_hooks_are_shadowed(self) -> None:
        class MW:
            def before_model(self, state: Any, runtime: Any) -> None: ...

        clone = with_managed_middleware(MW())
        # before_model was present -> replaced with an instance attribute.
        assert "before_model" in vars(clone)
        # after_model was never defined -> not shadowed.
        assert not hasattr(clone, "after_model")

    def test_adapts_runtime_without_identity(self) -> None:
        class MW:
            def before_model(self, state: Any, runtime: Any) -> None:
                state["seen"] = runtime

        clone = with_managed_middleware(MW())
        runtime = _runtime({})
        state: dict[str, Any] = {}
        clone.before_model(state, runtime=runtime)
        assert isinstance(state["seen"], ManagedToolRuntime)
        assert state["seen"].config is runtime.config
        assert not hasattr(state["seen"], "identity")
        assert state["seen"].backend is None

    def test_wraps_async_lifecycle_hook(self) -> None:
        class MW:
            async def abefore_model(self, state: Any, runtime: Any) -> None:
                state["seen"] = runtime

        clone = with_managed_middleware(MW())
        state: dict[str, Any] = {}
        asyncio.run(clone.abefore_model(state, runtime=_runtime(_AUTH)))
        assert state["seen"].server_info.principal.id == "u1"


class TestWithManagedMiddlewareWrapHooks:
    def test_augments_request_runtime_and_calls_handler(self) -> None:
        seen: dict[str, Any] = {}

        class MW:
            def wrap_tool_call(self, request: Any, handler: Any) -> Any:
                seen["runtime"] = request.runtime
                return handler(request)

        clone = with_managed_middleware(MW())
        request = ToolCallRequest(
            tool_call={"name": "calc", "args": {}, "id": "call"},
            tool=None,
            state={},
            runtime=_runtime(_AUTH),
        )
        result = clone.wrap_tool_call(request, lambda req: req.tool_call["name"])
        assert result == "calc"
        assert seen["runtime"].server_info.principal.id == "u1"

    def test_wraps_async_wrap_model_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_managed_middleware, "_ambient_config", lambda: {"configurable": _AUTH})
        seen: dict[str, Any] = {}

        class MW:
            async def awrap_model_call(self, request: Any, handler: Any) -> Any:
                seen["runtime"] = request.runtime
                return await handler(request)

        clone = with_managed_middleware(MW())

        async def handler(req: Any) -> str:
            return "ok"

        request = ModelRequest(
            model=FakeListChatModel(responses=["ok"]), messages=[], runtime=_runtime()
        )
        result = asyncio.run(clone.awrap_model_call(request, handler))
        assert result == "ok"
        assert seen["runtime"].server_info.principal.id == "u1"


class TestWrapMiddlewareWithManagedRuntime:
    def test_wraps_every_middleware(self) -> None:
        seen: list[Any] = []

        class MW:
            def before_model(self, state: Any, runtime: Any) -> None:
                seen.append(runtime.server_info.principal.id)

        wrapped = wrap_middleware_with_managed_runtime([MW(), MW()])
        for mw in wrapped:
            mw.before_model({}, runtime=_runtime(_AUTH))
        assert seen == ["u1", "u1"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
