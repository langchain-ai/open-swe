"""Report the sandbox a run opened to the backend behind the run's preparation.

Where the run's catalog lets it (``SandboxSpec.bind_after_prepare``), a run prepares and
makes its first model call while its sandbox is still being opened. Once it is open the
backend binds the thread to it (``runtime_api.loop.BIND_SANDBOX``), which only a tool call
needs. Every tool call waits for that, and for the sandbox's proxy config
(``rt.managed_sandbox``), as does the sandbox's boot and so everything that
reaches the sandbox (``rt.sandbox_setup``); a model call never does, and the run's end
does, so no thread is left with a sandbox it is not bound to. A sandbox that could not be
opened or bound ends the run at its first tool call, which tells the person why; a run
that calls no tool ends as it would have.
"""

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Final, Protocol

from runtime_api.endpoint import Transport, call
from runtime_api.loop import BIND_SANDBOX, BindSandbox

from rt.backend import Run, RunLocal
from rt.sandbox_setup import SandboxUnavailableError

logger = logging.getLogger(__name__)


class Opened(Protocol):
    """The thread's sandbox as the run opened it: its name, or why it could not be opened."""

    @property
    def sandbox_id(self) -> str | None: ...

    @property
    def error(self) -> str | None: ...


@dataclass(frozen=True)
class BoundSandbox:
    """How reporting a run's sandbox ended: bound, or why the run has none to work in."""

    error: str | None = None


class SandboxBinding:
    """One run's report of its sandbox to the backend; each run has its own (``SandboxBinding.of``)."""

    _RUNS: Final[RunLocal["SandboxBinding"]] = RunLocal(lambda: SandboxBinding())

    def __init__(self) -> None:
        self._reporting: asyncio.Future[BoundSandbox] | None = None

    @classmethod
    def of(cls, run: Run) -> "SandboxBinding":
        """The report of the sandbox ``run`` works in."""
        return cls._RUNS.get(run)

    def start(
        self,
        backend: Transport,
        run_id: str | None,
        opening: Awaitable[Opened],
    ) -> None:
        """Report the sandbox ``opening`` opens to ``backend`` in the background, once it is
        open."""
        self._reporting = asyncio.ensure_future(self._report(backend, run_id, opening))

    def abandon(self) -> None:
        """Stop reporting the sandbox: the run ended before it could use one."""
        if self._reporting is not None:
            self._reporting.cancel()
            self._reporting = None

    @property
    def pending(self) -> bool:
        """Whether the sandbox is still being opened or bound."""
        return self._reporting is not None and not self._reporting.done()

    async def settled(self) -> BoundSandbox | None:
        """How the report ended, once it has; None for a run that reported its sandbox as it
        prepared."""
        if self._reporting is None:
            return None
        return await asyncio.shield(self._reporting)

    async def usable(self) -> None:
        """Wait for the thread to be bound to its sandbox; raise ``SandboxUnavailableError``
        with why the run has none to work in."""
        bound = await self.settled()
        if bound is not None and bound.error is not None:
            raise SandboxUnavailableError(bound.error)

    async def _report(
        self,
        backend: Transport,
        run_id: str | None,
        opening: Awaitable[Opened],
    ) -> BoundSandbox:
        try:
            opened = await opening
            bound = await call(
                backend,
                BIND_SANDBOX,
                BindSandbox(
                    run_id=run_id,
                    sandbox_id=opened.sandbox_id,
                    sandbox_error=opened.error,
                ),
            )
        except Exception as exc:
            logger.warning("Could not bind the thread to its sandbox", exc_info=True)
            return BoundSandbox(
                error=f"{type(exc).__name__}: the thread could not be bound to its sandbox"
            )
        if bound.unreachable is not None:
            logger.warning(
                "The thread has no sandbox to work in", extra={"cause": bound.unreachable}
            )
            return BoundSandbox(error=bound.unreachable)
        return BoundSandbox()
