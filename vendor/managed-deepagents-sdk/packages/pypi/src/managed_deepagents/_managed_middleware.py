"""Add managed runtime fields without changing native middleware hook detection."""

from __future__ import annotations

import copy
import functools
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from types import new_class
from typing import TYPE_CHECKING, TypeVar, cast

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.config import get_config

from managed_deepagents._identity_runtime import (
    RuntimeConfigLike,
    build_managed_server_info,
    resolve_runtime_configurable,
    sanitize_configurable,
)
from managed_deepagents._managed_tools import (
    ManagedRuntimeExtras,
    adapt_managed_runtime,
    thread_overrides,
    with_managed_runtime,
)
from managed_deepagents._runtime_schedules import RuntimeSchedules

if TYPE_CHECKING:
    from managed_deepagents._managed_tools import RuntimeBackendFactory

__all__ = [
    "managed_middleware_overrides",
    "with_managed_middleware",
    "wrap_middleware_with_managed_runtime",
]

_MiddlewareT = TypeVar("_MiddlewareT", bound=AgentMiddleware)


#: Lifecycle hooks: ``(state, runtime)`` — ``runtime`` is injected as a kwarg.
_LIFECYCLE_HOOKS = (
    "before_agent",
    "before_model",
    "after_model",
    "after_agent",
    "abefore_agent",
    "abefore_model",
    "aafter_model",
    "aafter_agent",
)

#: Wrap hooks: ``(request, handler)`` — ``request.runtime`` carries the runtime.
_WRAP_HOOKS = (
    "wrap_model_call",
    "wrap_tool_call",
    "awrap_model_call",
    "awrap_tool_call",
)

_ASYNC_HOOKS = frozenset(
    {
        "abefore_agent",
        "abefore_model",
        "aafter_model",
        "aafter_agent",
        "awrap_model_call",
        "awrap_tool_call",
    }
)


def managed_middleware_overrides(
    runtime: RuntimeConfigLike | object | None,
    backend_factory: RuntimeBackendFactory | None = None,
) -> ManagedRuntimeExtras:
    """Runtime fields MDA overlays onto a middleware hook's runtime.

    Adds ``server_info.principal`` and — only when the runtime carries a ``config``
    (the ``ToolRuntime`` in ``wrap_tool_call``) — a copy whose ``configurable``
    has the reserved identity keys stripped. Lifecycle/model hooks read the
    ambient ``get_config()`` we cannot rewrite, so hygiene there is not
    applicable.
    """
    configurable = _configurable_from(runtime)
    overrides: ManagedRuntimeExtras = {
        "backend": None,
        "schedules": RuntimeSchedules(runtime),
    }
    if backend_factory is not None and runtime is not None:
        backend_runtime: object = (
            {"configurable": configurable} if configurable is not None else runtime
        )
        overrides["backend"] = backend_factory(backend_runtime)
    server_info = build_managed_server_info(runtime, configurable)
    if server_info is not None:
        overrides["server_info"] = server_info
    overrides.update(thread_overrides(runtime, configurable))
    config = getattr(runtime, "config", None)
    if isinstance(config, Mapping) and isinstance(configurable, Mapping):
        sanitized = sanitize_configurable(configurable)
        if len(sanitized) != len(configurable):
            overrides["config"] = {**dict(config), "configurable": sanitized}
    elif hasattr(runtime, "configurable") and isinstance(configurable, Mapping):
        sanitized = sanitize_configurable(configurable)
        if len(sanitized) != len(configurable):
            overrides["configurable"] = sanitized
    return overrides


class _ManagedMiddlewareBinding:
    """Bind managed runtime injection onto an author middleware instance.

    Owns the copy and hook shadowing required by LangChain's class-based hook
    detection. Prefer :func:`with_managed_middleware` as the public entry point.
    """

    def __init__(self, backend_factory: RuntimeBackendFactory | None = None) -> None:
        self._backend_factory = backend_factory

    def bind(self, middleware: _MiddlewareT) -> _MiddlewareT:
        """Return a shallow copy with overridden hooks shadowed for injection."""
        clone = copy.copy(middleware)
        tools = getattr(middleware, "tools", None)
        if isinstance(tools, Sequence):
            # ``AgentMiddleware.tools`` holds ``BaseTool`` instances, and managed
            # wrapping preserves each tool's type.
            _set_middleware_tools(
                clone,
                [with_managed_runtime(tool, self._backend_factory) for tool in tools],
            )
        for name in _LIFECYCLE_HOOKS:
            if _is_overridden(middleware, name):
                self._shadow_lifecycle_hook(clone, middleware, name)
        for name in _WRAP_HOOKS:
            if _is_overridden(middleware, name):
                self._shadow_wrap_hook(clone, middleware, name)
        return clone

    def _shadow_lifecycle_hook(self, clone: object, middleware: object, name: str) -> None:
        setattr(
            clone,
            name,
            _wrap_lifecycle(
                getattr(middleware, name),
                name in _ASYNC_HOOKS,
                self._backend_factory,
            ),
        )

    def _shadow_wrap_hook(self, clone: object, middleware: object, name: str) -> None:
        setattr(
            clone,
            name,
            _wrap_wrapper(
                getattr(middleware, name),
                name in _ASYNC_HOOKS,
                self._backend_factory,
            ),
        )


def _set_middleware_tools(middleware: AgentMiddleware, tools: Sequence[BaseTool]) -> None:
    try:
        middleware.tools = tools
        return
    except AttributeError:
        pass

    source_type = type(middleware)

    def define(namespace: dict[str, object]) -> None:
        namespace["__module__"] = source_type.__module__
        namespace["__slots__"] = ()
        namespace["tools"] = property(lambda _self: tools)

    tools_type = new_class(source_type.__name__, (source_type,), exec_body=define)
    object.__setattr__(middleware, "__class__", tools_type)


def with_managed_middleware(
    middleware: _MiddlewareT,
    backend_factory: RuntimeBackendFactory | None = None,
) -> _MiddlewareT:
    """Wrap a middleware so every hook sees ``runtime.server_info.principal`` (…).

    Only hooks the middleware actually overrides are shadowed. The caller's
    instance remains unchanged. Name, schema, and registered tools are preserved.
    """
    return _ManagedMiddlewareBinding(backend_factory).bind(middleware)


def wrap_middleware_with_managed_runtime(
    middleware: Sequence[_MiddlewareT],
    backend_factory: RuntimeBackendFactory | None = None,
) -> list[_MiddlewareT]:
    """Wrap every middleware in a list with :func:`with_managed_middleware`."""
    return [with_managed_middleware(entry, backend_factory) for entry in middleware]


def _configurable_from(
    runtime: RuntimeConfigLike | object | None,
) -> Mapping[str, object] | None:
    configurable = resolve_runtime_configurable(runtime)
    if configurable is not None:
        return configurable
    ambient = _ambient_config()
    if isinstance(ambient, Mapping):
        nested = ambient.get("configurable")
        return nested if isinstance(nested, Mapping) else None
    return None


def _ambient_config() -> RunnableConfig | None:
    try:
        return get_config()
    except RuntimeError:
        return None


def _is_overridden(middleware: object, name: str) -> bool:
    subclass_attr = getattr(type(middleware), name, None)
    base_attr = getattr(AgentMiddleware, name, None)
    return subclass_attr is not None and subclass_attr is not base_attr


def _augment_runtime_kwarg(
    kwargs: dict[str, object], backend_factory: RuntimeBackendFactory | None = None
) -> None:
    runtime = kwargs.get("runtime")
    if runtime is None:
        return
    overrides = managed_middleware_overrides(runtime, backend_factory)
    kwargs["runtime"] = adapt_managed_runtime(runtime, overrides)


def _augment_request(
    request: object, backend_factory: RuntimeBackendFactory | None = None
) -> object:
    if not isinstance(request, (ModelRequest, ToolCallRequest)):
        raise TypeError("expected a native model or tool request")
    runtime = request.runtime
    if runtime is None:
        return request
    overrides = managed_middleware_overrides(runtime, backend_factory)
    return replace(request, runtime=adapt_managed_runtime(runtime, overrides))


def _wrap_lifecycle(
    original: Callable[..., object],
    is_async: bool,
    backend_factory: RuntimeBackendFactory | None = None,
) -> Callable[..., object]:
    if is_async:

        @functools.wraps(original)
        async def awrapper(*args: object, **kwargs: object) -> object:
            _augment_runtime_kwarg(kwargs, backend_factory)
            return await cast("Callable[..., Awaitable[object]]", original)(*args, **kwargs)

        return awrapper

    @functools.wraps(original)
    def wrapper(*args: object, **kwargs: object) -> object:
        _augment_runtime_kwarg(kwargs, backend_factory)
        return original(*args, **kwargs)

    return wrapper


def _wrap_wrapper(
    original: Callable[..., object],
    is_async: bool,
    backend_factory: RuntimeBackendFactory | None = None,
) -> Callable[..., object]:
    if is_async:

        @functools.wraps(original)
        async def awrapper(
            request: object, handler: object, *args: object, **kwargs: object
        ) -> object:
            return await cast("Callable[..., Awaitable[object]]", original)(
                _augment_request(request, backend_factory), handler, *args, **kwargs
            )

        return awrapper

    @functools.wraps(original)
    def wrapper(request: object, handler: object, *args: object, **kwargs: object) -> object:
        return original(_augment_request(request, backend_factory), handler, *args, **kwargs)

    return wrapper
