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

MDA writes the sandbox's whole proxy config, through its own proxy config sync, as the
backend hands it over (``runtime_api.sandbox_proxy``): GitHub's rules, with a token the
backend minted for the run's scope, and its tool channel's capability, which names the
sandbox, so it is asked for once the sandbox is open, and again before the token expires.
The run's sandbox is resolved before that, so it leaves the proxy config alone
(``_openswe_no_manage_proxy``) and the commands MDA runs there write none: until the first
write a new sandbox keeps the rules it was created with, its cloud identity among them, and
an external sandbox keeps its own throughout (``SandboxSpec.external``). The first write
waits for the sandbox to boot, so it is made behind the run's preparation and first model
call, and the run's first tool call waits for it, whichever tool it is, since the backend
may run it in the sandbox: it is retried for as long as the host takes to suspend an idle
sandbox, while the sandbox API takes no proxy config, and then a failure ends the run at
that call, since the backend writes none of it. It writes it again every few minutes before
a model or tool call, for the commands the backend runs in the sandbox.
"""

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, runtime_checkable

import httpx2
from deepagents.backends import CompositeBackend
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.runtime import Runtime
from langgraph.types import Command
from langsmith.sandbox import (
    ResourceCreationError,
    ResourceNotFoundError,
    ResourceTimeoutError,
    SandboxAPIError,
    SandboxClientError,
    SandboxConnectionError,
    SandboxNotReadyError,
)
from managed_deepagents import ManagedServerRuntime, define_sandbox
from managed_deepagents._sandbox_proxy import SandboxProxyConfigSync  # noqa: PLC2701
from managed_deepagents.sandbox import SandboxDefinition
from pydantic import JsonValue
from runtime_api.catalog import SandboxCreate, SandboxSpec
from runtime_api.endpoint import call
from runtime_api.loop import SANDBOX_UNREACHABLE, SandboxGone
from runtime_api.sandbox_proxy import SANDBOX_PROXY, SandboxProxyRequest

from rt.backend import (
    BackendCallError,
    Run,
    RunContext,
    RunLocal,
    current_run,
)
from rt.sandbox_binding import SandboxBinding
from rt.sandbox_setup import SandboxUnavailableError
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
_SECRET = re.compile(
    r"(?i)\b((?:token|api[_-]?key|secret|password|authorization)\s*[:=]\s*(?:bearer\s+)?|bearer\s+)"
    r"[^\s,;&\"']+"
)
# Agent Auth renews the GitHub token MDA reads, and the commands the backend runs in
# the sandbox write no proxy config, so MDA writes it again this often.
_PROXY_REWRITE_SECONDS: Final = 300.0
_PROXY_RETRY_SECONDS: Final = 60.0
# How long before the backend's GitHub token expires MDA asks for a fresh one: three
# rewrites' worth, so a run that makes model calls never writes an expired token.
_BACKEND_TOKEN_MARGIN: Final = timedelta(minutes=15)
# The sandbox API takes no proxy config while the host suspends an idle sandbox, which takes
# up to about three minutes: the write a sandbox's boot makes outlasts that, as the
# backend's own does.
PROXY_WRITE_BACKOFF_SECONDS: Final = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 30.0, 30.0, 30.0, 30.0)
PROXY_WRITE_MAX_ELAPSED_SECONDS: Final = 180.0
# No sandbox to write on: writing again changes nothing.
_PERMANENT_WRITE_FAILURES: Final = (ResourceNotFoundError,)


@runtime_checkable
class _Openable(Protocol):
    """MDA's handle on the run's sandbox, which adopts or asks for it now."""

    async def aopen(self, *, wait: bool = True) -> str: ...


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


class SandboxProxyWriter:
    """The proxy config MDA writes on a run's sandbox, and when it writes it next."""

    def __init__(self) -> None:
        # The config the backend hands over, which names the sandbox, so it is known once the
        # sandbox is open, and when the GitHub token in it expires.
        self._backend_config: dict[str, JsonValue] | None = None
        self._backend_expires_at: datetime | None = None
        # The next ask is for a newly minted GitHub token: GitHub refused the last one.
        self._force_refresh = False
        self._due_at = 0.0
        self._written = False
        # The sandbox whose first write is left to its boot, until it lands, and that write.
        self._deferred: str | None = None
        self._writing: asyncio.Future[None] | None = None
        # What MDA last wrote on each sandbox in this run, so an unchanged config is not
        # written again.
        self._sync = SandboxProxyConfigSync()

    def config(self) -> dict[str, object] | None:
        """All of it, as the backend handed it over; None until it has."""
        return None if self._backend_config is None else dict(self._backend_config)

    @property
    def written(self) -> bool:
        """Whether a write landed on the run's sandbox."""
        return self._written

    def _stale(self) -> bool:
        """Whether to ask the backend for it again: its GitHub token expires soon."""
        expires_at = self._backend_expires_at
        return expires_at is not None and expires_at - datetime.now(UTC) < _BACKEND_TOKEN_MARGIN

    def defer(self, sandbox_id: str) -> None:
        """Leave the first write on sandbox ``sandbox_id`` to its boot (``write_deferred``).

        None is due before it lands, so a model call never waits for the sandbox to boot.
        """
        self._deferred = sandbox_id
        self._writing = None

    async def write_deferred(self, runtime: object) -> None:
        """Make the write left to the sandbox's boot, once for every call that waits for it;
        raises when it could not be made, so none of them goes ahead."""
        if self._deferred is None:
            return
        if self._writing is None:
            self._writing = asyncio.ensure_future(self.write_first(runtime, self._deferred))
        await asyncio.shield(self._writing)

    async def write_first(self, runtime: object, sandbox_id: str) -> None:
        """Write it on sandbox ``sandbox_id`` for the first time, retrying while it fails for as
        long as the host takes to suspend an idle sandbox; raises, the cause logged, after."""
        deadline = time.monotonic() + PROXY_WRITE_MAX_ELAPSED_SECONDS
        for backoff in (*PROXY_WRITE_BACKOFF_SECONDS, None):
            try:
                await self.write(runtime)
            except GraphBubbleUp:
                raise
            except Exception as exc:
                delay = None if backoff is None else backoff * random.uniform(0.8, 1.2)
                if (
                    isinstance(exc, _PERMANENT_WRITE_FAILURES)
                    or delay is None
                    or time.monotonic() + delay > deadline
                ):
                    logger.error(
                        "Could not write the sandbox's proxy config, so the run ends",
                        extra={"sandbox_id": sandbox_id, "cause": _cause(exc)},
                    )
                    raise
                logger.warning(
                    "Writing the sandbox's proxy config failed; retrying",
                    extra={"sandbox_id": sandbox_id, "delay_seconds": delay, "cause": _cause(exc)},
                )
                await asyncio.sleep(delay)
            else:
                if self._deferred == sandbox_id:
                    self._deferred = None
                return

    async def write(self, runtime: object) -> None:
        """Have MDA write it on the run's sandbox now, once the sandbox has booted; raises when
        it could not.

        MDA resolves the run's sandbox once, before the backend's part is known, so the
        definition it resolved never carries it: MDA's proxy config sync writes it here,
        outside any command.
        """
        self._due_at = time.monotonic() + _PROXY_RETRY_SECONDS
        planned = _planned(runtime)
        if planned is None:
            raise BackendCallError("This run has no sandbox")
        name = await OpenedSandbox.open(getattr(runtime, "backend", None))
        if self._backend_config is None or self._stale():
            request = SandboxProxyRequest(
                run_id=run_id(), sandbox_id=name, force_refresh=self._force_refresh
            )
            answer = await call(planned[0].backend, SANDBOX_PROXY, request)
            self._backend_config = answer.config
            self._backend_expires_at = answer.expires_at
        spec = planned[1]
        written = definition(spec.create, spec.sandbox_id, self.config())
        await self._sync.sync(written, name, None)
        self._written = True
        self._due_at = time.monotonic() + _PROXY_REWRITE_SECONDS

    @classmethod
    def of(cls, runtime: object) -> SandboxProxyWriter | None:
        """The writer of the proxy config of the run in ``runtime``; None outside a run Robin
        started, or where its sandbox is external."""
        return _written_proxy(runtime)

    async def rewrite_fresh(self, runtime: object) -> bool:
        """Write it again now, with a GitHub token the backend mints anew because GitHub
        refused the last one; whether the config carries one. Raises when it could not."""
        self._backend_config = None
        self._force_refresh = True
        try:
            await self.write(runtime)
        finally:
            self._force_refresh = False
        return self._backend_expires_at is not None

    async def keep_fresh(self, runtime: object) -> None:
        """Write it again when due; a failure is tried again later, the sandbox keeping the last.

        None is due while the sandbox's boot has its first write to make.
        """
        if self._deferred is not None or time.monotonic() < self._due_at:
            return
        try:
            await self.write(runtime)
        except GraphBubbleUp:
            raise
        except Exception:
            logger.warning("Could not write the sandbox's proxy config", exc_info=True)


# Each run's own, never the shared graph's.
_PROXIES: Final = RunLocal(SandboxProxyWriter)


def _written_proxy(runtime: object) -> SandboxProxyWriter | None:
    """The proxy config MDA writes on the run's sandbox; None outside a run Robin started, or
    where its sandbox is external."""
    planned = _planned(runtime)
    if planned is None or planned[1].external:
        return None
    return _PROXIES.get(planned[0])


def _resolved(runtime: ManagedServerRuntime) -> SandboxDefinition | None:
    """The sandbox the run in ``runtime`` uses, as its catalog says; None outside a run Robin
    started, which then has no sandbox.

    MDA calls it once per run, before the sandbox is open and so before the backend hands
    over its part of the proxy config: the run's sandbox leaves its proxy config alone, and
    ``SandboxProxyWriter`` writes it once it is known.
    """
    planned = _planned(runtime)
    if planned is None:
        return None
    spec = planned[1]
    return definition(spec.create, spec.sandbox_id)


def definition(
    create: SandboxCreate,
    sandbox_id: str | None = None,
    proxy_config: dict[str, object] | None = None,
) -> SandboxDefinition:
    """The sandbox MDA keeps for the run's thread, created from ``create``.

    A task worker's run is an async subagent of its coordinator's, so MDA gives it the
    coordinator's sandbox. MDA writes ``proxy_config`` on it before each command; without
    one it leaves the sandbox's proxy config alone, as on the sandbox a run resolves.
    """
    return define_sandbox(
        _openswe_sandbox_id=sandbox_id,
        _openswe_no_manage_proxy=proxy_config is None,
        _openswe_create_fields=dict(create.extra_fields),
        proxy_config=proxy_config,
        snapshot_id=create.snapshot_id,
        idle_ttl_seconds=create.idle_ttl_seconds,
        delete_after_stop_seconds=create.delete_after_stop_seconds,
        fs_capacity_bytes=create.fs_capacity_bytes,
        vcpus=create.vcpus,
        mem_bytes=create.mem_bytes,
    )


def declared_sandbox() -> SandboxDefinition:
    """The sandbox every run's catalog picks from."""
    return define_sandbox(default_timeout=EXECUTE_TIMEOUT_SECONDS, resolve=_resolved)


@dataclass(frozen=True)
class OpenedSandbox:
    """The thread's sandbox as the run opened it: its name, or why it could not be opened."""

    sandbox_id: str | None = None
    error: str | None = None

    @staticmethod
    async def open(backend: object, *, wait: bool = True) -> str:
        """Adopt or create the run's sandbox now and return its name; without ``wait``, before
        it boots, its first use waiting for it. ``backend`` is ``runtime.backend``, which tools
        may see composited."""
        while isinstance(backend, CompositeBackend):
            backend = backend.default
        if not isinstance(backend, _Openable):
            raise TypeError("This run has no managed sandbox")
        return await backend.aopen(wait=wait)


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
    Writing the proxy config waits for the sandbox to boot, so the first write is left to the
    boot; where the run prepares in the sandbox, that boot comes before it prepares
    (``BackendRunMiddleware._proxied``).
    """
    opened = await _opening(getattr(runtime, "backend", None), wait=False)
    if opened.sandbox_id is not None and (proxy := _written_proxy(runtime)) is not None:
        proxy.defer(opened.sandbox_id)
    return opened


async def booted_sandbox(runtime: Runtime) -> OpenedSandbox:
    """The run's sandbox once it has booted, with the proxy config MDA writes on a sandbox the
    run opened, or why it could not.

    The backend writes none of that config, so a sandbox MDA could not write it on may have
    none, nor the workspace's egress rules: it counts as one that never booted. So does one
    the run reports behind its preparation that the thread could not be bound to.
    """
    planned = _planned(runtime)
    if planned is not None:
        bound = await SandboxBinding.of(planned[0]).settled()
        if bound is not None and bound.error is not None:
            return OpenedSandbox(error=bound.error)
    if (proxy := _written_proxy(runtime)) is not None:
        try:
            await proxy.write_deferred(runtime)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return OpenedSandbox(error=_cause(exc))
    return await _opening(getattr(runtime, "backend", None), wait=True)


async def _opening(backend: object, *, wait: bool) -> OpenedSandbox:
    """``backend``'s sandbox, retried while the sandbox service fails transiently.

    A bound sandbox that is gone (``SandboxMissingError``) is neither retried nor
    replaced: the backend tells the person it was deleted.
    """
    deadline = time.monotonic() + OPEN_MAX_ELAPSED_SECONDS
    for backoff in OPEN_BACKOFF_SECONDS:
        try:
            return OpenedSandbox(sandbox_id=await OpenedSandbox.open(backend, wait=wait))
        except Exception as exc:
            delay = backoff * random.uniform(0.8, 1.2)
            if not _transient(exc) or time.monotonic() + delay > deadline:
                return _not_opened(exc)
            logger.warning(
                "Opening the thread's sandbox failed transiently; retrying",
                exc_info=True,
                extra={"delay_seconds": delay},
            )
            await asyncio.sleep(delay)
    try:
        return OpenedSandbox(sandbox_id=await OpenedSandbox.open(backend, wait=wait))
    except Exception as exc:
        return _not_opened(exc)


class ManagedSandboxMiddleware(AgentMiddleware):
    """Keep the proxy config MDA writes on the thread's sandbox fresh, and hold every tool
    call until the thread is bound to the sandbox and the config is written there."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        # A model call never opens the sandbox, nor waits for one the run is still opening or
        # could not open: it keeps fresh only a config already written there.
        if (proxy := _written_proxy(request.runtime)) is not None and proxy.written:
            await proxy.keep_fresh(request.runtime)
        return await handler(request)

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
        # No call goes ahead of the thread's binding to its sandbox: the backend may run it
        # there, with the GitHub token binding puts there.
        if (planned := _planned(request.runtime)) is not None:
            await self._bound(planned[0])
        proxy = _written_proxy(request.runtime)
        if proxy is not None:
            # No call goes ahead of the sandbox's first proxy config: the backend may run it
            # there.
            await proxy.write_deferred(request.runtime)
            # A resumed run reaches a tool call before any model call, and its sandbox may
            # hold a token that expired while it waited.
            await proxy.keep_fresh(request.runtime)
        return await handler(request)
