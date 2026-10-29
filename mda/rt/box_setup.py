"""Ready the thread's box for the run's git work: its work dir and the bot's git identity.

MDA runs no hook of the project's as it creates or adopts a box, so each run readies
the box MDA opened for it, in the background as the run starts, with one command that
leaves a readied box as it was. The run asks for a new box without waiting for it to
boot, so readying it first waits for the boot. A call that needs the box waits for the
boot (``booted``), and a tool that may change a checkout for the readying (``done``),
both in ``rt.file_tools``; nothing else does.
"""

import asyncio
import logging
import shlex
from collections.abc import Awaitable, Callable
from typing import Final, Protocol

from deepagents.backends.protocol import SandboxBackendProtocol
from runtime_api import box

from rt.backend import Run, RunLocal

logger = logging.getLogger(__name__)

_SETUP: Final = (
    f"mkdir -p {shlex.quote(box.WORK_DIR)} && test -w {shlex.quote(box.WORK_DIR)}; "
    f"git config --global user.name {shlex.quote(box.GIT_NAME)} && "
    f"git config --global user.email {shlex.quote(box.GIT_EMAIL)}"
)


class Boot(Protocol):
    """Why a box could not boot, or None once it has."""

    @property
    def error(self) -> str | None: ...


class BoxUnavailableError(RuntimeError):
    """The thread's box could not boot; its message is why."""


class BoxSetup:
    """One run's readying of its box; each run has its own (``box_setup``)."""

    def __init__(self) -> None:
        self._booted: asyncio.Future[Boot] | None = None
        self._ready: asyncio.Task[None] | None = None

    def start(
        self,
        files: object,
        boot: Callable[[SandboxBackendProtocol], Awaitable[Boot]] | None = None,
    ) -> None:
        """Ready the box ``files`` reaches in the background, once ``boot`` says it has
        booted; without ``boot``, it already has."""
        if not isinstance(files, SandboxBackendProtocol):
            return
        self._booted = None if boot is None else asyncio.ensure_future(boot(files))
        self._ready = asyncio.ensure_future(self._setup(files))

    async def booted(self) -> None:
        """Wait for the box to boot; raise ``BoxUnavailableError`` with why it could not."""
        if self._booted is None:
            return
        boot = await asyncio.shield(self._booted)
        if boot.error is not None:
            raise BoxUnavailableError(boot.error)

    async def done(self) -> None:
        await self.booted()
        if self._ready is not None:
            await asyncio.shield(self._ready)

    async def _setup(self, files: SandboxBackendProtocol) -> None:
        try:
            await self.booted()
        except BoxUnavailableError:
            # The calls that need the box report it.
            return
        try:
            result = await files.aexecute(_SETUP)
        except Exception:
            logger.warning("Could not ready the thread's box", exc_info=True)
            return
        if result.exit_code:
            logger.warning(
                "Could not ready the thread's box",
                extra={"exit_code": result.exit_code, "output": result.output[-500:]},
            )


_SETUPS: Final = RunLocal(BoxSetup)


def box_setup(run: Run) -> BoxSetup:
    """The readying of the box ``run`` works in."""
    return _SETUPS.get(run)
