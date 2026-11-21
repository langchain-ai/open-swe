"""The thread's sandbox as MDA's own sandbox: declared, and opened as the run starts.

The graph declares one sandbox (``sandbox``) for every run, and MDA resolves each run's from
its catalog, once, at the run's first model call or sandbox use: which sandbox it uses and
the options MDA creates it with (``SandboxSpec``), its thread's own, one the backend made
that MDA adopts by name, or a task worker's coordinator's. A run whose catalog names none
does not start. MDA keeps one sandbox per thread whatever the backend's options become,
which apply only when MDA creates it. Before the run prepares, the sandbox is asked for and
its name reported, so the backend binds the thread to it; it boots while the run prepares
and calls the model, and the first call that needs it waits for it. Where preparing reaches
no sandbox (``SandboxSpec.bind_after_prepare``), the sandbox is asked for while the run
prepares and calls the model, and reported once it is open (``rt.sandbox_binding``); the
run's first tool call, whichever it is, waits for that. Opening and booting are retried
while the sandbox service fails transiently; otherwise the backend hears why, from preparing
or from the call that needed the sandbox. A thread and its sandbox are one: nothing replaces
a sandbox the thread is bound to, so one that is gone leaves the run unable to open it, and
the backend says why.

MDA creates the sandbox with the proxy config the catalog names (``SandboxSpec.proxy_config``)
and writes it there before each command it runs, once per run: the backend works it out
as it plans the run, the same every time, with no credential in it, so it carries the
cloud identity rules LangSmith fixed when it created the sandbox, and LangSmith asks the
backend's proxy callback for GitHub's and the tool channel's credentials as requests go
out. An external sandbox's catalog names none: it is an existing sandbox
(``existing_sandbox``), whose proxy config MDA leaves alone when the run declares none.
Where the run prepares in the sandbox, which the
backend's commands reach without MDA, the config is written before it prepares, retried
while the sandbox service fails transiently, as opening the sandbox is.
"""

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Final, Protocol, runtime_checkable

import httpx
import httpx2
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import ExecuteResponse
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.runtime import Runtime
from langgraph.types import Command
from langsmith.sandbox import (
    ResourceCreationError,
    ResourceTimeoutError,
    SandboxAPIError,
    SandboxClientError,
    SandboxConnectionError,
    SandboxNotReadyError,
)
from managed_deepagents import ManagedServerRuntime, RunEndingError, define_sandbox
from managed_deepagents._sandbox_proxy import ProxyConfigRefusedError  # noqa: PLC2701
from managed_deepagents.sandbox import SandboxDefinition, normalize_sandbox_options
from pydantic import JsonValue
from runtime_api.catalog import SandboxCreate, SandboxSpec
from runtime_api.loop import SANDBOX_UNREACHABLE, SandboxGone

from rt.backend import BackendCallError, Run, RunContext, current_run
from rt.sandbox_binding import SandboxBinding
from rt.sandbox_setup import SandboxUnavailableError
from rt.tool_errors import SandboxUnreachableError, sandbox_unreachable
from rt.tools import run_id
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)

# As the backend's sandbox: a command without a timeout of its own gets five minutes.
EXECUTE_TIMEOUT_SECONDS: Final = 300
# A partial sandbox-service outage passes within a minute or two; a run waits that long.
OPEN_BACKOFF_SECONDS: Final = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
OPEN_MAX_ELAPSED_SECONDS: Final = 90.0
_CAUSE_LIMIT: Final = 300
# Creation failures the service, not the sandbox's options, caused; an image or config one is not.
_UNSCHEDULED: Final = frozenset({"Unschedulable", "ServiceUnavailable"})
# What the sandbox API answers a proxy config write with while it is overloaded, or while
# the host suspends an idle sandbox, as the backend's own writes retry them.
_RETRIED_WRITE_STATUS: Final = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_REFUSED_STATUS = re.compile(r"\(HTTP (\d{3})\b")
_SECRET = re.compile(
    r"(?i)\b((?:token|api[_-]?key|secret|password|authorization)\s*[:=]\s*(?:bearer\s+)?|bearer\s+)"
    r"[^\s,;&\"']+"
)
# What MDA runs where the run prepares in the sandbox, so it writes the proxy config there.
_NO_OP: Final = "true"


@runtime_checkable
class _Openable(Protocol):
    """MDA's handle on the run's sandbox, which adopts or asks for it now."""

    async def aopen(self, *, wait: bool = True) -> str: ...

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse: ...


def _run_of(runtime: object) -> Run:
    context = getattr(runtime, "context", None)
    # A hook's backend is bound to the run's configurable, not its runtime, and the runtime
    # MDA resolves the run's sandbox with carries the context as sent, unparsed: the run in
    # progress is the one they act for.
    return context.run() if isinstance(context, RunContext) else current_run()


def _planned(runtime: object) -> tuple[Run, SandboxSpec] | None:
    """The run in ``runtime``, and the sandbox its catalog names; None outside a run Robin
    started with one."""
    try:
        run = _run_of(runtime)
        return run, run.sandbox
    except BackendCallError, RuntimeError:
        # Not started yet, or not a Robin run, so nothing may open a sandbox for it.
        return None


def _resolved(runtime: ManagedServerRuntime) -> SandboxDefinition | None:
    """The sandbox the run in ``runtime`` uses, as its catalog says; None outside a run Robin
    started, which then has no sandbox. MDA calls it once per run."""
    planned = _planned(runtime)
    if planned is None:
        return None
    spec = planned[1]
    return definition(spec.create, spec.sandbox_id, spec.proxy_config)


def definition(
    create: SandboxCreate,
    sandbox_id: str | None = None,
    proxy_config: Mapping[str, JsonValue] | None = None,
) -> SandboxDefinition:
    """The sandbox MDA keeps for the run's thread, created from ``create``.

    ``sandbox_id`` is the sandbox the backend recorded for the thread, or for a task worker
    its coordinator's: MDA opens only that one and never creates it, so one that is gone
    ends the run. Without one MDA opens, or creates, its own sandbox for the thread; a task
    worker's run is an async subagent of its coordinator's, so that is the coordinator's.
    MDA creates it with ``proxy_config`` and writes that before each command; without one,
    which only an external sandbox has, MDA leaves that existing sandbox's proxy config
    alone. The catalog's ``proxy_config`` carries the workspace's, federated cloud rules
    included, so ``create``'s own is not sent. Its commands' environment
    (``create.env_vars``) goes in its own run config. ``create``'s other ``create_sandbox``
    arguments go as they are; the typed ones win.
    """
    fields = create.create_arguments()
    fields.pop("proxy_config", None)
    run_config: dict[str, object] | None = None
    if create.env_vars:
        declared_run = fields.pop("run_config", None)
        declared_run = declared_run if isinstance(declared_run, dict) else {}
        env_vars = declared_run.get("env_vars")
        env_vars = env_vars if isinstance(env_vars, dict) else {}
        run_config = {**declared_run, "env_vars": {**env_vars, **create.env_vars}}
    declared = define_sandbox(
        existing_sandbox=sandbox_id,
        proxy_config=None if proxy_config is None else dict(proxy_config),
        run_config=run_config,
        snapshot_id=create.snapshot_id,
        idle_ttl_seconds=create.idle_ttl_seconds,
        delete_after_stop_seconds=create.delete_after_stop_seconds,
        fs_capacity_bytes=create.fs_capacity_bytes,
        vcpus=create.vcpus,
        mem_bytes=create.mem_bytes,
    )
    return replace(declared, options=normalize_sandbox_options({**fields, **declared.options}))


def declared_sandbox() -> SandboxDefinition:
    """The sandbox every run's catalog picks from."""
    return define_sandbox(default_timeout=EXECUTE_TIMEOUT_SECONDS, resolve=_resolved)


@dataclass(frozen=True)
class OpenedSandbox:
    """The thread's sandbox as the run opened it: its name, or why it could not be opened."""

    sandbox_id: str | None = None
    error: str | None = None

    @staticmethod
    def _managed(backend: object) -> _Openable:
        """MDA's handle on the run's sandbox in ``backend``, ``runtime.backend``, which tools
        may see composited."""
        while isinstance(backend, CompositeBackend):
            backend = backend.default
        if not isinstance(backend, _Openable):
            raise TypeError("This run has no managed sandbox")
        return backend

    @classmethod
    async def open(cls, backend: object, *, wait: bool = True) -> str:
        """Adopt or create the run's sandbox now and return its name; without ``wait``, before
        it boots, its first use waiting for it."""
        return await cls._managed(backend).aopen(wait=wait)

    @classmethod
    async def write_proxy(cls, backend: object) -> None:
        """Have MDA write the run's proxy config on its sandbox now, as it does before the
        first command it runs there; raises when it could not."""
        await cls._managed(backend).aexecute(_NO_OP)

    @staticmethod
    async def retried[T](attempt: Callable[[], Awaitable[T]], *, failing: str) -> T:
        """``attempt``'s result, retried while the sandbox service fails transiently, for as
        long as a partial outage lasts; raises its last error. ``failing`` logs each retry."""
        deadline = time.monotonic() + OPEN_MAX_ELAPSED_SECONDS
        for backoff in OPEN_BACKOFF_SECONDS:
            try:
                return await attempt()
            except Exception as exc:
                delay = backoff * random.uniform(0.8, 1.2)
                if not _transient(exc) or time.monotonic() + delay > deadline:
                    raise
                logger.warning(failing, exc_info=True, extra={"delay_seconds": delay})
                await asyncio.sleep(delay)
        return await attempt()


def _transient(exc: BaseException) -> bool:
    """Whether opening the sandbox again may succeed: the service, not the request, failed."""
    if isinstance(
        exc,
        SandboxConnectionError
        | SandboxNotReadyError
        | SandboxAPIError
        | ResourceTimeoutError
        | httpx2.TransportError
        | TimeoutError
        | ConnectionError,
    ):
        return True
    if isinstance(exc, ResourceCreationError) and exc.error_type in _UNSCHEDULED:
        return True
    if isinstance(exc, SandboxClientError):
        # The SDK maps some 5xx answers to errors of its own, or to none, such as a 503.
        status = getattr(getattr(exc.__cause__, "response", None), "status_code", None)
        return isinstance(status, int) and status >= 500
    if isinstance(exc, ProxyConfigRefusedError):
        # MDA's proxy config write keeps the sandbox API's answer only in its message.
        refused = _REFUSED_STATUS.search(str(exc))
        return refused is not None and int(refused[1]) in _RETRIED_WRITE_STATUS
    if isinstance(exc, httpx.TransportError):
        return True
    if exc.__suppress_context__ and exc.__context__ is not None:
        # MDA's proxy config write hides what failed behind its own error, credentials and all.
        return _transient(exc.__context__)
    return False


def _cause(exc: BaseException) -> str:
    """``exc`` as the backend reports it to the person: its type and message, secrets removed."""
    message = _SECRET.sub(r"\1[redacted]", " ".join(str(exc).split()))
    cause = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return cause if len(cause) <= _CAUSE_LIMIT else f"{cause[: _CAUSE_LIMIT - 1]}…"


def _not_opened(exc: Exception) -> OpenedSandbox:
    logger.warning("Could not open the thread's sandbox", exc_info=exc)
    return OpenedSandbox(error=_cause(exc))


async def opened_sandbox(runtime: Runtime) -> OpenedSandbox:
    """Adopt the thread's sandbox or ask for it now, without waiting for it to boot.

    The backend reports a sandbox MDA could not open as unreachable when it prepares, with the
    cause, or the run's first tool call does where the run reports the sandbox behind its
    preparation; one that cannot boot is reported by the first call that needs it
    (``booted_sandbox``).
    """
    return await _opening(getattr(runtime, "backend", None), wait=False)


async def booted_sandbox(runtime: Runtime) -> OpenedSandbox:
    """The run's sandbox once it has booted, with the proxy config the run's catalog names
    written there, or why it could not be.

    The backend's commands while the run prepares reach the sandbox without MDA, which writes
    the config only before its own, so it is written here first. A sandbox MDA could not
    write it on counts as one that never booted. So does one the run reports behind its
    preparation that the thread could not be bound to.
    """
    planned = _planned(runtime)
    if planned is not None:
        bound = await SandboxBinding.of(planned[0]).settled()
        if bound is not None and bound.error is not None:
            return OpenedSandbox(error=bound.error)
    backend = getattr(runtime, "backend", None)
    opened = await _opening(backend, wait=True)
    if opened.error is not None or planned is None or planned[1].proxy_config is None:
        return opened
    try:
        await OpenedSandbox.retried(
            lambda: OpenedSandbox.write_proxy(backend),
            failing="Writing the sandbox's proxy config failed transiently; retrying",
        )
    except GraphBubbleUp:
        raise
    except Exception as exc:
        logger.warning("Could not write the sandbox's proxy config", exc_info=True)
        return OpenedSandbox(error=_cause(exc))
    return opened


async def _opening(backend: object, *, wait: bool) -> OpenedSandbox:
    """``backend``'s sandbox, retried while the sandbox service fails transiently.

    A bound sandbox LangSmith can't find (``SandboxMissingError``: deleted, or outside the
    workspace MDA's key sees) is neither retried nor replaced: the backend tells the person.
    """
    try:
        sandbox_id = await OpenedSandbox.retried(
            lambda: OpenedSandbox.open(backend, wait=wait),
            failing="Opening the thread's sandbox failed transiently; retrying",
        )
    except Exception as exc:
        return _not_opened(exc)
    return OpenedSandbox(sandbox_id=sandbox_id)


def _unbooted(exc: Exception) -> RunEndingError:
    """What a sandbox that could not boot for a tool call ends the run with, named so the
    backend's failure reply gives the person why: one that stopped answering keeps its
    ``sandbox_unreachable`` reason."""
    if sandbox_unreachable(exc):
        return SandboxUnreachableError(str(exc))
    return SandboxUnavailableError(str(exc))


class ManagedSandboxMiddleware(AgentMiddleware):
    """Hold every tool call until the thread is bound to the sandbox the run opened and the
    sandbox has booted."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    @staticmethod
    async def _bound(run: Run) -> None:
        """Wait for ``run``'s thread to be bound to the sandbox the run opened behind its
        preparation; a run left with none ends here, once the person is told why."""
        try:
            await SandboxBinding.of(run).usable()
        except SandboxUnavailableError as exc:
            logger.exception("The run has no sandbox to work in")
            run.outbox.notify(SANDBOX_UNREACHABLE, SandboxGone(run_id=run_id(), reason=str(exc)))
            raise

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        # No call goes ahead of the thread's binding to its sandbox, nor of its boot: the
        # backend may run it there, and its proxy callback answers only for a sandbox a thread
        # is bound to. A sandbox that cannot boot ends the run here.
        if (planned := _planned(request.runtime)) is not None:
            await self._bound(planned[0])
            try:
                await OpenedSandbox.open(getattr(request.runtime, "backend", None))
            except GraphBubbleUp, RunEndingError:
                raise
            except Exception as exc:
                # The run ends, rather than the error reaching the model as the call's.
                raise _unbooted(exc) from exc
        return await handler(request)
