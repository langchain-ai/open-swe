"""What one run's sandbox ``resolve`` sees and returned.

A cached graph serves many runs, so a run's runtime can't be baked into it. MDA
creates a ``SandboxRun`` for each run's call of the graph and exposes it through
``CURRENT_SANDBOX_RUN`` while the run's steps execute, the way it carries
``APPROVED_USER``.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, TypeVar, cast

from deepagents.middleware.filesystem import FilesystemState
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)

if TYPE_CHECKING:
    from managed_deepagents._managed_tools import ManagedServerRuntime

_T = TypeVar("_T")


class Missing:
    """The type of ``MISSING``: nothing resolved yet."""

    __slots__ = ()


MISSING = Missing()


class SandboxRun:
    """One run's runtime, and each sandbox resolution made for it."""

    def __init__(self, runtime: ManagedServerRuntime) -> None:
        self.runtime = runtime
        self._lock = threading.Lock()
        self._resolved: dict[object, object] = {}

    def peek(self, key: object) -> object:
        """The value resolved for ``key`` in this run, or ``MISSING``; never resolves."""
        return self._resolved.get(key, MISSING)

    def once(self, key: object, resolve: Callable[[], _T]) -> _T:
        """Resolve ``key`` once for this run; concurrent first uses share one call.

        A failure is not kept, so the next use calls ``resolve`` again.
        """
        with self._lock:
            if key in self._resolved:
                return cast("_T", self._resolved[key])
            value = resolve()
            self._resolved[key] = value
            return value


#: The run whose step is executing; a nested managed graph keeps its parent's.
CURRENT_SANDBOX_RUN: ContextVar[SandboxRun | None] = ContextVar(
    "mda_current_sandbox_run", default=None
)


#: What a run without a sandbox gets for ``execute``, and for opening its sandbox.
NO_SANDBOX = "This run has no sandbox."


def _tool_name(tool: object) -> object:
    return tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)


def _without_execute(request: ModelRequest[None], has_sandbox: bool) -> ModelRequest[None]:
    if has_sandbox:
        return request
    tools = [tool for tool in request.tools if _tool_name(tool) != "execute"]
    return request.override(tools=tools) if len(tools) != len(request.tools) else request


class _NoSandboxExecuteMiddleware(AgentMiddleware[FilesystemState, None, object]):
    """Hide ``execute`` from the model in a run whose sandbox ``resolve`` returned None.

    deepagents keeps ``execute`` whenever the compiled backend looks like a
    sandbox, and a graph whose sandbox declares ``resolve`` always has one. Asking
    here is also what resolves the run's sandbox at its first model call.

    Its state schema adds the ``files`` key a run without a sandbox keeps its
    files in; deepagents adds it only for a backend that is a ``StateBackend``.
    """

    state_schema = FilesystemState

    def __init__(
        self,
        has_sandbox: Callable[[], bool],
        ahas_sandbox: Callable[[], Awaitable[bool]],
    ) -> None:
        super().__init__()
        self._has_sandbox = has_sandbox
        self._ahas_sandbox = ahas_sandbox

    @property
    def name(self) -> str:
        return "mda-sandbox-resolve"

    def wrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[object]],
    ) -> ModelCallResult[object]:
        return handler(_without_execute(request, self._has_sandbox()))

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelCallResult[object]:
        return await handler(_without_execute(request, await self._ahas_sandbox()))


def create_no_sandbox_execute_middleware(
    has_sandbox: Callable[[], bool],
    ahas_sandbox: Callable[[], Awaitable[bool]],
) -> AgentMiddleware[FilesystemState, None, object]:
    """Remove ``execute`` from model calls of runs that resolved no sandbox."""
    return _NoSandboxExecuteMiddleware(has_sandbox, ahas_sandbox)
