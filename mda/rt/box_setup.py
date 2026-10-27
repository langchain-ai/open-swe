"""Ready the thread's box for the run's git work: its work dir and the bot's git identity.

MDA runs no hook of the project's as it creates or adopts a box, so each run readies
the box MDA opened for it, in the background as the run starts, with one command that
leaves a readied box as it was. A tool that may change a checkout waits for it
(``rt.file_tools``); nothing else does.
"""

import asyncio
import logging
import shlex
from typing import Final

from deepagents.backends.protocol import SandboxBackendProtocol
from runtime_api import box

from rt.backend import Run, RunLocal

logger = logging.getLogger(__name__)

_SETUP: Final = (
    f"mkdir -p {shlex.quote(box.WORK_DIR)} && test -w {shlex.quote(box.WORK_DIR)}; "
    f"git config --global user.name {shlex.quote(box.GIT_NAME)} && "
    f"git config --global user.email {shlex.quote(box.GIT_EMAIL)}"
)


class BoxSetup:
    """One run's readying of its box; each run has its own (``box_setup``)."""

    def __init__(self) -> None:
        self._ready: asyncio.Task[None] | None = None

    def start(self, files: object) -> None:
        """Ready the box ``files`` reaches, in the background."""
        if isinstance(files, SandboxBackendProtocol):
            self._ready = asyncio.ensure_future(self._setup(files))

    async def done(self) -> None:
        if self._ready is not None:
            await asyncio.shield(self._ready)

    @staticmethod
    async def _setup(files: SandboxBackendProtocol) -> None:
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
