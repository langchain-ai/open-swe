"""Add managed fields to native tool and middleware runtimes.

ToolNode injects a native ToolRuntime. The wrapper copies its dataclass fields
into a ManagedToolRuntime before it calls authored code.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Generic,
    TypedDict,
    TypeVar,
    cast,
    overload,
)

from deepagents.backends.protocol import BackendProtocol
from langchain.tools import ToolRuntime
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg
from langgraph.runtime import Runtime
from langgraph.typing import ContextT
from langgraph_sdk.runtime import ServerRuntime, _ExecutionRuntime, _ReadRuntime
from pydantic import GetPydanticSchema
from pydantic_core import core_schema
from typing_extensions import TypeVar as DefaultTypeVar

from managed_deepagents._channels.runtime import RuntimeChannel  # noqa: TC001 - dataclass schema
from managed_deepagents._identity_runtime import (
    RuntimeConfigLike,
    build_managed_server_info,
    resolve_runtime_configurable,
    sanitize_configurable,
)
from managed_deepagents._managed_thread import ManagedThread, current_run_thread, no_thread
from managed_deepagents._runtime_schedules import RuntimeSchedules
from managed_deepagents.identity import ManagedServerInfo  # noqa: TC001 - dataclass schema

if TYPE_CHECKING:
    from managed_deepagents.define_deep_agent import DeepAgentTool

RuntimeBackendFactory = Callable[[object], BackendProtocol | None]


_ContextT = DefaultTypeVar("_ContextT", default=Any)
_StateT = DefaultTypeVar("_StateT", default=Any)
_AppContextT = DefaultTypeVar("_AppContextT", default=dict[str, object])


class ManagedRuntimeExtras(TypedDict, total=False):
    """Managed fields and sanitized configuration for native runtimes."""

    server_info: ManagedServerInfo
    thread: ManagedThread
    channel: RuntimeChannel | None
    configurable: Mapping[str, object]
    config: RunnableConfig
    backend: BackendProtocol | None
    schedules: RuntimeSchedules


# Match the frozen native Runtime; ty cannot detect its decorator options.
@dataclass(frozen=True, slots=True)
class ManagedRuntime(Runtime[ContextT], Generic[ContextT]):  # ty: ignore[invalid-frozen-dataclass-subclass]
    """Native middleware runtime with exact authored context and managed fields."""

    server_info: ManagedServerInfo | None = None  # ty: ignore[invalid-attribute-override]
    thread: ManagedThread = field(default_factory=no_thread)
    #: Deprecated: use ``runtime.thread.origin.channel``. Removed in 0.11.
    channel: RuntimeChannel | None = None
    backend: BackendProtocol | None = None
    schedules: RuntimeSchedules = field(default_factory=RuntimeSchedules)


@dataclass
class ManagedToolRuntime(ToolRuntime[_ContextT, _StateT], Generic[_ContextT, _StateT]):
    """Native tool runtime; type parameters follow context, then state."""

    server_info: ManagedServerInfo | None = None  # ty: ignore[invalid-mutable-override]
    thread: ManagedThread = field(default_factory=no_thread)
    #: Deprecated: use ``runtime.thread.origin.channel``. Removed in 0.11.
    channel: RuntimeChannel | None = None
    backend: BackendProtocol | None = None
    schedules: RuntimeSchedules = field(default_factory=RuntimeSchedules)

    @classmethod
    def __class_getitem__(cls, parameters: object) -> object:
        return Annotated[
            super().__class_getitem__(parameters),  # ty: ignore[invalid-type-form]
            GetPydanticSchema(
                lambda _source, _handler: core_schema.is_instance_schema(ToolRuntime)
            ),
            InjectedToolArg,
        ]

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: object, _handler: object
    ) -> core_schema.CoreSchema:
        # ToolNode injects the base runtime before MDA adapts it.
        return core_schema.is_instance_schema(ToolRuntime)


@dataclass(frozen=True, slots=True, kw_only=True)
class _ManagedExecutionRuntime(_ExecutionRuntime[_AppContextT | None], Generic[_AppContextT]):
    thread: ManagedThread = field(default_factory=no_thread)
    #: Deprecated: use ``runtime.thread.origin.channel``. Removed in 0.11.
    channel: RuntimeChannel | None = None
    backend: BackendProtocol | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class _ManagedReadRuntime(_ReadRuntime[_AppContextT | None], Generic[_AppContextT]):
    #: Graph inspection is not a run, so it has no thread.
    thread: None = None
    #: Deprecated: use ``runtime.thread.origin.channel``. Removed in 0.11.
    channel: RuntimeChannel | None = None
    backend: BackendProtocol | None = None


ManagedServerRuntime = _ManagedExecutionRuntime[_AppContextT] | _ManagedReadRuntime[_AppContextT]


def adapt_server_runtime(
    runtime: ServerRuntime[Any], context: object, thread: ManagedThread | None
) -> ManagedServerRuntime[Any]:
    values = {field.name: getattr(runtime, field.name) for field in fields(runtime) if field.init}
    if not isinstance(runtime, _ExecutionRuntime):
        return _ManagedReadRuntime(**values)
    values["context"] = context
    if thread is not None:
        values["thread"] = thread
        values["channel"] = thread.origin.channel if thread.origin is not None else None
    return _ManagedExecutionRuntime(**values)


ManagedDeepAgentRuntime = ManagedToolRuntime


__all__ = [
    "ManagedDeepAgentRuntime",
    "ManagedRuntime",
    "ManagedRuntimeExtras",
    "ManagedToolRuntime",
    "adapt_managed_runtime",
    "managed_runtime_overrides",
    "thread_overrides",
    "with_managed_runtime",
    "wrap_tools_with_managed_runtime",
]

_ToolT = TypeVar("_ToolT")
_R = TypeVar("_R")


def managed_runtime_overrides(
    runtime: RuntimeConfigLike | object | None,
    backend_factory: RuntimeBackendFactory | None = None,
) -> ManagedRuntimeExtras:
    """Runtime fields MDA overlays onto a tool's runtime.

    Adds ``server_info.principal`` (when an actor resolved) and — when the
    runtime carries a ``config`` — a copy whose ``configurable`` has the reserved
    identity keys stripped so a tool can't read a client-spoofed
    ``user_id``. Managed optional fields are always supplied, and
    ``schedules`` is bound to the original runtime's trusted caller.
    """
    overrides: ManagedRuntimeExtras = {
        "backend": None,
        "schedules": RuntimeSchedules(runtime),
    }
    if backend_factory is not None and runtime is not None:
        overrides["backend"] = backend_factory(runtime)
    configurable = resolve_runtime_configurable(runtime)
    server_info = build_managed_server_info(runtime, configurable)
    if server_info is not None:
        overrides["server_info"] = server_info
    overrides.update(thread_overrides(runtime, configurable))
    if isinstance(configurable, Mapping):
        sanitized = sanitize_configurable(configurable)
        if len(sanitized) != len(configurable):
            config = getattr(runtime, "config", None)
            if isinstance(config, Mapping):
                overrides["config"] = {**dict(config), "configurable": sanitized}
            elif hasattr(runtime, "configurable"):
                overrides["configurable"] = sanitized
    return overrides


def thread_overrides(
    runtime: object, configurable: Mapping[str, object] | None
) -> ManagedRuntimeExtras:
    """``thread``, and the deprecated ``channel`` alias of its origin's channel."""
    existing = getattr(runtime, "thread", None)
    if isinstance(existing, ManagedThread) and existing.id:
        thread = existing
    else:
        thread = current_run_thread(runtime, configurable).thread
    return {
        "thread": thread,
        "channel": thread.origin.channel if thread.origin is not None else None,
    }


@overload
def adapt_managed_runtime(
    runtime: ToolRuntime[_ContextT, _StateT], extras: ManagedRuntimeExtras
) -> ManagedToolRuntime[_ContextT, _StateT]: ...


@overload
def adapt_managed_runtime(
    runtime: Runtime[ContextT], extras: ManagedRuntimeExtras
) -> ManagedRuntime[ContextT]: ...


@overload
def adapt_managed_runtime(
    runtime: object, extras: ManagedRuntimeExtras
) -> ManagedRuntime[Any] | ManagedToolRuntime[Any, Any]: ...


def adapt_managed_runtime(
    runtime: object, extras: ManagedRuntimeExtras
) -> ManagedRuntime[Any] | ManagedToolRuntime[Any, Any]:
    """Preserve native fields when adding managed runtime fields."""
    if not isinstance(runtime, (Runtime, ToolRuntime)):
        raise TypeError("expected a native LangGraph or LangChain runtime")
    runtime_type = ManagedToolRuntime if isinstance(runtime, ToolRuntime) else ManagedRuntime
    values = {field.name: getattr(runtime, field.name) for field in fields(runtime) if field.init}
    values.update(extras)
    return runtime_type(**values)


def with_managed_runtime(
    managed_tool: _ToolT,
    backend_factory: RuntimeBackendFactory | None = None,
) -> _ToolT:
    """Wrap a tool so its injected ``runtime`` carries the managed primitives.

    Wraps ``func``/``coroutine`` (preserving the signature so ``ToolNode`` still
    detects and injects the ``runtime`` argument) and, at call time, swaps that
    argument for a managed native subclass exposing ``runtime.server_info.principal``.
    Name, args schema, and tool class are untouched. Tools without a wrapped
    callable pass through.
    """
    updates: dict[str, object] = {}
    func = getattr(managed_tool, "func", None)
    if callable(func):
        updates["func"] = _augment_sync(cast("Callable[..., object]", func), backend_factory)
    coroutine = getattr(managed_tool, "coroutine", None)
    if callable(coroutine):
        updates["coroutine"] = _augment_async(
            cast("Callable[..., Awaitable[object]]", coroutine), backend_factory
        )
    if not updates:
        return managed_tool

    # Pydantic tools (StructuredTool) copy cleanly; fall back to attribute
    # assignment for any other tool object.
    model_copy = getattr(managed_tool, "model_copy", None)
    if callable(model_copy):
        return cast("_ToolT", model_copy(update=updates))
    for key, value in updates.items():
        setattr(managed_tool, key, value)
    return managed_tool


def wrap_tools_with_managed_runtime(
    tools: Sequence[DeepAgentTool],
    backend_factory: RuntimeBackendFactory | None = None,
) -> list[DeepAgentTool]:
    """Wrap every tool in a list with :func:`with_managed_runtime`."""
    wrapped: list[DeepAgentTool] = []
    for managed_tool in tools:
        if isinstance(managed_tool, dict):
            # Provider-native (server-side) tool specs have no callable to wrap.
            wrapped.append(managed_tool)
            continue
        normalized = managed_tool
        if callable(managed_tool) and not hasattr(managed_tool, "invoke"):
            from langchain_core.tools import tool as create_tool  # noqa: PLC0415

            normalized = create_tool(managed_tool)
        wrapped.append(with_managed_runtime(normalized, backend_factory))
    return wrapped


def _inject_managed_runtime(
    kwargs: dict[str, object], backend_factory: RuntimeBackendFactory | None = None
) -> None:
    for name, runtime in kwargs.items():
        if isinstance(runtime, ToolRuntime):
            overrides = managed_runtime_overrides(runtime, backend_factory)
            kwargs[name] = adapt_managed_runtime(runtime, overrides)


def _augment_sync(
    func: Callable[..., _R], backend_factory: RuntimeBackendFactory | None = None
) -> Callable[..., _R]:
    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> _R:
        _inject_managed_runtime(kwargs, backend_factory)
        return func(*args, **kwargs)

    return wrapper


def _augment_async(
    coroutine: Callable[..., Awaitable[_R]],
    backend_factory: RuntimeBackendFactory | None = None,
) -> Callable[..., Awaitable[_R]]:
    @functools.wraps(coroutine)
    async def wrapper(*args: object, **kwargs: object) -> _R:
        _inject_managed_runtime(kwargs, backend_factory)
        return await coroutine(*args, **kwargs)

    return wrapper
