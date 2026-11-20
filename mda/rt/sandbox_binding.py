"""Report the sandbox a run opened to the backend behind the run's preparation.

Where the run's catalog lets it (``SandboxSpec.bind_after_prepare``), a run prepares and
makes its first model call while its sandbox is still being opened. Once it is open the
backend binds the thread to it and gives it its GitHub token
(``runtime_api.loop.BIND_SANDBOX``), which only a tool call needs. Every tool call waits
for that (``rt.managed_sandbox``), as does the sandbox's boot and so everything that
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
from runtime_api.loop import (
    BIND_SANDBOX,
    REFRESH_GITHUB_PROXY,
    BindSandbox,
    GitHubProxyRefreshed,
    RefreshGitHubProxy,
)

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
    # The token the sandbox's GitHub proxy was given, where one was due.
    github_proxy: GitHubProxyRefreshed | None = None


class SandboxBinding:
    """One run's report of its sandbox to the backend; each run has its own (``SandboxBinding.of``)."""

    _RUNS: Final[RunLocal["SandboxBinding"]] = RunLocal(lambda: SandboxBinding())

    def __init__(self) -> None:
        self._reporting: asyncio.Future[BoundSandbox] | None = None
        self._told: set[str] = set()

    @classmethod
    def of(cls, run: Run) -> "SandboxBinding":
        """The report of the sandbox ``run`` works in."""
        return cls._RUNS.get(run)

    def start(
        self,
        backend: Transport,
        run_id: str | None,
        opening: Awaitable[Opened],
        *,
        refresh_github_proxy: bool,
    ) -> None:
        """Report the sandbox ``opening`` opens to ``backend`` in the background, once it is
        open, asking for the sandbox's GitHub token where the run says one is due."""
        self._told = set()
        self._reporting = asyncio.ensure_future(
            self._report(backend, run_id, opening, refresh_github_proxy)
        )

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

    def take_github_proxy(self) -> GitHubProxyRefreshed | None:
        """The token binding put on the sandbox, once, for the run's loop to keep until it is due."""
        bound = self._taken("github_proxy")
        return None if bound is None else bound.github_proxy

    def _taken(self, what: str) -> BoundSandbox | None:
        if self._reporting is None or not self._reporting.done() or what in self._told:
            return None
        self._told.add(what)
        return self._reporting.result()

    async def _report(
        self,
        backend: Transport,
        run_id: str | None,
        opening: Awaitable[Opened],
        refresh_github_proxy: bool,
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
                    refresh_github_proxy=refresh_github_proxy,
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
        github_proxy = bound.github_proxy
        if github_proxy is None and refresh_github_proxy and opened.sandbox_id is not None:
            github_proxy = await self._refreshed(backend, run_id)
        return BoundSandbox(github_proxy=github_proxy)

    @staticmethod
    async def _refreshed(backend: Transport, run_id: str | None) -> GitHubProxyRefreshed | None:
        """Ask once more for the token binding could not put on the sandbox, before any tool
        call runs; the run's loop asks again later, and the run goes on without it."""
        try:
            return await call(backend, REFRESH_GITHUB_PROXY, RefreshGitHubProxy(run_id=run_id))
        except Exception:
            logger.warning("Could not refresh the sandbox's GitHub token", exc_info=True)
            return None
