"""The git identity a sandbox's commits are authored with."""

import asyncio
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from deepagents.backends.protocol import SandboxBackendProtocol

from coding_agent.utils.startup_trace import aphase


@dataclass(frozen=True, slots=True)
class GitIdentity:
    """Who a sandbox commits as, written as its ``--global`` git config."""

    name: str
    email: str

    async def apply(self, backend: SandboxBackendProtocol) -> None:
        await backend.aexecute(
            f"git config --global user.name {shlex.quote(self.name)} && "
            f"git config --global user.email {shlex.quote(self.email)}",
        )

    @asynccontextmanager
    async def applied(
        self, thread_id: str | None, backend: SandboxBackendProtocol
    ) -> AsyncIterator[None]:
        """Write the identity while the body installs credentials.

        The identity needs the box, not the credentials, and the cost is the
        round trip rather than the two `git config` calls — on a cold sandbox
        that round trip is over a second of the critical path before the first
        model call. A body that raises has lost the sandbox, so the write is
        dropped rather than joined.
        """

        async def run() -> None:
            async with aphase(thread_id, "sandbox.git_identity"):
                await self.apply(backend)

        task = asyncio.create_task(run())
        try:
            yield
        except BaseException:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            raise
        await task
