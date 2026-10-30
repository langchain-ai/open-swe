"""Ready the thread's sandbox for the run's git work: its work dir and the bot's git identity.

MDA runs no hook of the project's as it creates or adopts a sandbox, so each run readies
the sandbox MDA opened for it, in the background as the run starts, with one command that
leaves a readied sandbox as it was. The run asks for a new sandbox without waiting for it to
boot, so readying it first waits for the boot. A call that needs the sandbox waits for the
boot (``booted``), and a tool that may change a checkout for the readying (``done``),
both in ``rt.file_tools``; the turn's git baseline (``rt.git_evidence``) waits for the
readying too, since it reads the work dir, so a sandbox that never booted is never reached
for, and so never made again.
"""

import asyncio
import logging
import shlex
from collections.abc import Awaitable, Callable
from typing import Final, Protocol

from deepagents.backends.protocol import SandboxBackendProtocol
from runtime_api.sandbox_setup import GIT_EMAIL, GIT_NAME, WORK_DIR

from rt.backend import Run, RunLocal

logger = logging.getLogger(__name__)

# The git identity is written even without a work dir; the exit status says if either failed.
_SETUP: Final = (
    f"mkdir -p {shlex.quote(WORK_DIR)} && test -w {shlex.quote(WORK_DIR)}; work_dir=$?; "
    f"git config --global user.name {shlex.quote(GIT_NAME)} && "
    f"git config --global user.email {shlex.quote(GIT_EMAIL)} && exit $work_dir"
)


class Boot(Protocol):
    """Why a sandbox could not boot, or None once it has."""

    @property
    def error(self) -> str | None: ...


class SandboxUnavailableError(RuntimeError):
    """The thread's sandbox could not boot; its message is why."""


class SandboxSetup:
    """One run's readying of its sandbox; each run has its own (``sandbox_setup``)."""

    def __init__(self) -> None:
        self._booted: asyncio.Future[Boot] | None = None
        self._ready: asyncio.Task[None] | None = None

    def start(
        self,
        files: object,
        boot: Callable[[SandboxBackendProtocol], Awaitable[Boot]] | None = None,
    ) -> None:
        """Ready the sandbox ``files`` reaches in the background, once ``boot`` says it has
        booted; without ``boot``, it already has."""
        if not isinstance(files, SandboxBackendProtocol):
            return
        self._booted = None if boot is None else asyncio.ensure_future(boot(files))
        self._ready = asyncio.ensure_future(self._setup(files))

    async def booted(self) -> None:
        """Wait for the sandbox to boot; raise ``SandboxUnavailableError`` with why it could not."""
        if self._booted is None:
            return
        boot = await asyncio.shield(self._booted)
        if boot.error is not None:
            raise SandboxUnavailableError(boot.error)

    async def done(self) -> None:
        await self.booted()
        if self._ready is not None:
            await asyncio.shield(self._ready)

    async def _setup(self, files: SandboxBackendProtocol) -> None:
        try:
            await self.booted()
        except SandboxUnavailableError:
            # The calls that need the sandbox report it.
            return
        try:
            result = await files.aexecute(_SETUP)
        except Exception:
            logger.warning("Could not ready the thread's sandbox", exc_info=True)
            return
        if result.exit_code:
            logger.warning(
                "Could not ready the thread's sandbox",
                extra={"exit_code": result.exit_code, "output": result.output[-500:]},
            )


_SETUPS: Final = RunLocal(SandboxSetup)


def sandbox_setup(run: Run) -> SandboxSetup:
    """The readying of the sandbox ``run`` works in."""
    return _SETUPS.get(run)
