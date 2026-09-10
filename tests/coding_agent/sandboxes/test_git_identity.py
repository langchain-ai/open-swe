"""The git identity is written while credentials are being installed.

The identity needs the sandbox, not the credentials, so running it afterwards
put a full sandbox round trip — over a second on a cold box — on the critical
path before the run's first model call.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from coding_agent.sandboxes.git_identity import GitIdentity

IDENTITY = GitIdentity("open-swe[bot]", "open-swe@users.noreply.github.com")


def _backend(started: asyncio.Event) -> MagicMock:
    async def aexecute(_command: str) -> str:
        started.set()
        return "ok"

    return MagicMock(id="sandbox-new", aexecute=AsyncMock(side_effect=aexecute))


@pytest.mark.asyncio
async def test_quotes_the_identity_values() -> None:
    backend = MagicMock(id="sandbox-new", aexecute=AsyncMock())

    await IDENTITY.apply(backend)

    command = backend.aexecute.await_args.args[0]
    assert "git config --global user.name 'open-swe[bot]'" in command
    assert "user.email open-swe@users.noreply.github.com" in command


@pytest.mark.asyncio
async def test_identity_is_written_while_the_body_runs() -> None:
    started = asyncio.Event()
    backend = _backend(started)

    async with IDENTITY.applied("thread-overlap", backend):
        # Serial ordering would leave the identity unstarted until this returns.
        await asyncio.wait_for(started.wait(), timeout=2)

    backend.aexecute.assert_awaited_once()


@pytest.mark.asyncio
async def test_identity_failure_fails_the_sandbox() -> None:
    backend = MagicMock(
        id="sandbox-new", aexecute=AsyncMock(side_effect=RuntimeError("identity failed"))
    )

    with pytest.raises(RuntimeError, match="identity failed"):
        async with IDENTITY.applied("thread-identity-fails", backend):
            pass


@pytest.mark.asyncio
async def test_a_failed_body_does_not_leave_the_identity_write_running() -> None:
    release = asyncio.Event()

    async def aexecute(_command: str) -> str:
        await release.wait()
        return "ok"

    backend = MagicMock(id="sandbox-new", aexecute=AsyncMock(side_effect=aexecute))

    with pytest.raises(RuntimeError, match="proxy failed"):
        async with IDENTITY.applied("thread-proxy-fails", backend):
            raise RuntimeError("proxy failed")

    # Cancelled with the sandbox, rather than left behind to report into a run
    # that has already given up on the box.
    assert not release.is_set()
