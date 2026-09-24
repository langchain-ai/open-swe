"""The bot's fallback git identity is written off the critical path of startup.

Every run rewrites it, because a reused sandbox can lose its ``--global`` config,
and waiting for that round trip put seconds before the run's first model call.
Sandbox readiness does not wait for it; the thread's proxy holds its first
command until the write has finished, so no agent command runs without it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from agent.github.sandbox_access import SandboxGitHubAccess
from agent.sandboxes import lifecycle
from agent.sandboxes.lifecycle import (
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    recreate_sandbox_for_thread,
)
from agent.sandboxes.state import SandboxBackendProxy, set_sandbox_backend
from agent.utils import startup_trace
from agent.utils.startup_trace import flush_phases
from agent.workspaces.store import Workspace, script_command

THREAD_ID = "thread-git-identity"
# Bounds waits that must finish, so a regression fails instead of hanging.
_HANG_TIMEOUT = 5


class _Sandbox(SandboxBackendProtocol):
    """Runs commands instantly, except the identity write, which waits to be released.

    A stalled write is never released; it ends only at its own timeout, if it has one.
    """

    def __init__(self, sandbox_id: str = "sandbox-1") -> None:
        self._sandbox_id = sandbox_id
        self.ran: list[str] = []
        self.identity_started = asyncio.Event()
        self.release_identity = asyncio.Event()
        self.identity_error: Exception | None = None
        self.identity_exit_code = 0
        self.identity_stalls = False
        self.identity_cancelled = False

    @property
    def id(self) -> str:
        return self._sandbox_id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if command.startswith("git config --global"):
            self.identity_started.set()
            if self.identity_stalls and timeout:
                return ExecuteResponse(output=f"Command timed out after {timeout}s.", exit_code=124)
            try:
                await self.release_identity.wait()
            except asyncio.CancelledError:
                self.identity_cancelled = True
                raise
            if self.identity_error is not None:
                raise self.identity_error
            self.ran.append("identity")
            return ExecuteResponse(output="", exit_code=self.identity_exit_code)
        self.ran.append(command)
        return ExecuteResponse(output="", exit_code=0)


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> _Sandbox:
    """The LangSmith sandbox that a thread with no bound sandbox gets."""
    box = _Sandbox()
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.setattr(lifecycle, "get_sandbox_metadata", AsyncMock(return_value={}))
    monkeypatch.setattr(lifecycle, "load_workspace", AsyncMock(return_value=None))
    monkeypatch.setattr(lifecycle, "create_sandbox", AsyncMock(return_value=box))
    monkeypatch.setattr(
        lifecycle, "workspace_token", AsyncMock(return_value=SandboxGitHubAccess("token"))
    )
    monkeypatch.setattr(lifecycle, "configure_sandbox_proxy", AsyncMock())
    monkeypatch.setattr(lifecycle, "record_proxy_token_expiry", MagicMock())
    monkeypatch.setattr(lifecycle.client.threads, "update", AsyncMock())
    return box


@pytest.fixture
def stale_image(sandbox: _Sandbox, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    """A workspace whose image has aged out, so the new ``sandbox`` runs its update script."""
    workspace = Workspace(
        slug="base", update_script="git pull", snapshot_status="ready", snapshot_id="snap-1"
    )
    monkeypatch.setattr(lifecycle, "load_workspace", AsyncMock(return_value=workspace))
    monkeypatch.setattr(lifecycle, "maybe_start_update", AsyncMock())
    return workspace


async def _execute(proxy: SandboxBackendProxy, command: str) -> int | None:
    return (await proxy.aexecute(command)).exit_code


async def _execute_with_offload(proxy: SandboxBackendProxy, command: str) -> int | None:
    offload = await proxy.aexecute_with_offload(command, "/tmp/capture", max_inline_bytes=1_000)
    return offload.response.exit_code


@pytest.mark.parametrize("bound", [False, True], ids=["create", "reconnect"])
async def test_sandbox_is_ready_while_the_identity_is_still_being_written(
    sandbox: _Sandbox, monkeypatch: pytest.MonkeyPatch, bound: bool
) -> None:
    if bound:
        monkeypatch.setattr(
            lifecycle, "get_sandbox_metadata", AsyncMock(return_value={"sandbox_id": sandbox.id})
        )

    proxy = await asyncio.wait_for(ensure_sandbox_for_thread(THREAD_ID), timeout=_HANG_TIMEOUT)

    assert proxy.current is sandbox
    await asyncio.wait_for(sandbox.identity_started.wait(), timeout=_HANG_TIMEOUT)
    assert sandbox.ran == []


@pytest.mark.parametrize("run", [_execute, _execute_with_offload])
async def test_first_command_waits_for_the_identity_write(
    sandbox: _Sandbox,
    monkeypatch: pytest.MonkeyPatch,
    run: Callable[[SandboxBackendProxy, str], Awaitable[int | None]],
) -> None:
    proxy_configured = asyncio.Event()

    async def configure(*_args: object, **_kwargs: object) -> None:
        await proxy_configured.wait()

    monkeypatch.setattr(lifecycle, "configure_sandbox_proxy", configure)
    # The same handle the agent's tools execute through, started as build_agent does.
    proxy = get_cached_sandbox_backend(
        THREAD_ID, reconnect=lambda: ensure_sandbox_for_thread(THREAD_ID)
    )
    proxy.start()

    # Issued while the sandbox is still starting, before the write is handed over.
    command = asyncio.create_task(run(proxy, "git commit -m change"))
    await asyncio.wait_for(sandbox.identity_started.wait(), timeout=_HANG_TIMEOUT)
    proxy_configured.set()
    await asyncio.wait_for(proxy.ready(), timeout=_HANG_TIMEOUT)

    assert sandbox.ran == []
    sandbox.release_identity.set()
    assert await asyncio.wait_for(command, timeout=_HANG_TIMEOUT) == 0
    assert sandbox.ran == ["identity", "git commit -m change"]


async def test_failed_identity_write_is_logged_and_commands_still_run(
    sandbox: _Sandbox, caplog: pytest.LogCaptureFixture
) -> None:
    sandbox.identity_error = RuntimeError("git is not installed")
    sandbox.release_identity.set()
    caplog.set_level(logging.WARNING)

    proxy = await asyncio.wait_for(ensure_sandbox_for_thread(THREAD_ID), timeout=_HANG_TIMEOUT)
    first = await asyncio.wait_for(proxy.aexecute("git status"), timeout=_HANG_TIMEOUT)
    second = await asyncio.wait_for(proxy.aexecute("git log"), timeout=_HANG_TIMEOUT)

    assert (first.exit_code, second.exit_code) == (0, 0)
    assert sandbox.ran == ["git status", "git log"]
    [failure] = [
        record
        for record in caplog.records
        if record.exc_info and record.exc_info[1] is sandbox.identity_error
    ]
    assert failure.levelno == logging.WARNING
    assert getattr(failure, "thread_id", None) == THREAD_ID
    assert getattr(failure, "sandbox_id", None) == sandbox.id


async def test_lost_sandbox_cancels_its_identity_write_and_nothing_waits_on_it(
    sandbox: _Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A box lost while reconnecting must not hold up its replacement's first command."""
    lost = _Sandbox("sandbox-lost")
    sandbox.release_identity.set()

    async def connect_or_create(sandbox_id: str | None = None, **_create: object) -> _Sandbox:
        return lost if sandbox_id == lost.id else sandbox

    async def configure(sandbox_id: str, *_args: object, **_kwargs: object) -> None:
        if sandbox_id == lost.id:
            await lost.identity_started.wait()
            raise RuntimeError("proxy unreachable")

    monkeypatch.setattr(
        lifecycle, "get_sandbox_metadata", AsyncMock(return_value={"sandbox_id": lost.id})
    )
    monkeypatch.setattr(lifecycle, "create_sandbox", connect_or_create)
    monkeypatch.setattr(lifecycle, "configure_sandbox_proxy", configure)

    proxy = await asyncio.wait_for(
        ensure_sandbox_for_thread(THREAD_ID, allow_replacement=True), timeout=_HANG_TIMEOUT
    )
    result = await asyncio.wait_for(proxy.aexecute("git status"), timeout=_HANG_TIMEOUT)

    assert proxy.current is sandbox
    assert lost.identity_cancelled
    assert result.exit_code == 0
    assert sandbox.ran == ["identity", "git status"]


async def test_rebound_thread_does_not_wait_on_the_previous_boxs_identity_write(
    sandbox: _Sandbox,
) -> None:
    await asyncio.wait_for(ensure_sandbox_for_thread(THREAD_ID), timeout=_HANG_TIMEOUT)
    await asyncio.wait_for(sandbox.identity_started.wait(), timeout=_HANG_TIMEOUT)
    replacement = _Sandbox("sandbox-replacement")
    proxy = set_sandbox_backend(THREAD_ID, replacement)

    result = await asyncio.wait_for(proxy.aexecute("git status"), timeout=_HANG_TIMEOUT)

    assert result.exit_code == 0
    assert (sandbox.ran, replacement.ran) == ([], ["git status"])


async def test_command_held_through_a_rebind_runs_on_the_new_box(sandbox: _Sandbox) -> None:
    proxy = await asyncio.wait_for(ensure_sandbox_for_thread(THREAD_ID), timeout=_HANG_TIMEOUT)
    await asyncio.wait_for(sandbox.identity_started.wait(), timeout=_HANG_TIMEOUT)
    command = asyncio.create_task(proxy.aexecute("git status"))
    await asyncio.sleep(0)  # the command resolves this box and waits on its write
    replacement = _Sandbox("sandbox-replacement")
    set_sandbox_backend(THREAD_ID, replacement)
    sandbox.release_identity.set()

    await asyncio.wait_for(command, timeout=_HANG_TIMEOUT)

    assert (sandbox.ran, replacement.ran) == (["identity"], ["git status"])


async def test_recreate_binds_the_new_box_without_writing_its_identity_twice(
    sandbox: _Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _Sandbox("sandbox-old")
    set_sandbox_backend(THREAD_ID, old)
    monkeypatch.setattr(lifecycle, "get_sandbox_id_from_metadata", AsyncMock(return_value=old.id))

    rebound = await asyncio.wait_for(recreate_sandbox_for_thread(THREAD_ID), timeout=_HANG_TIMEOUT)
    sandbox.release_identity.set()
    proxy = get_cached_sandbox_backend(THREAD_ID)
    result = await asyncio.wait_for(proxy.aexecute("git status"), timeout=_HANG_TIMEOUT)

    assert rebound == (old.id, sandbox.id)
    assert result.exit_code == 0
    assert sandbox.ran == ["identity", "git status"]


async def test_new_sandbox_writes_its_identity_before_a_stale_images_update_script(
    sandbox: _Sandbox, stale_image: Workspace
) -> None:
    startup = asyncio.create_task(ensure_sandbox_for_thread(THREAD_ID, workspace_slug="base"))
    await asyncio.wait_for(sandbox.identity_started.wait(), timeout=_HANG_TIMEOUT)
    sandbox.release_identity.set()
    await asyncio.wait_for(startup, timeout=_HANG_TIMEOUT)

    assert sandbox.ran == ["identity", script_command("git pull", "update", stale_image.repos)]


async def test_stale_images_wait_for_the_identity_write_is_a_startup_phase(
    sandbox: _Sandbox, stale_image: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(startup_trace, "_PHASES", {})

    startup = asyncio.create_task(ensure_sandbox_for_thread(THREAD_ID, workspace_slug="base"))
    await asyncio.wait_for(sandbox.identity_started.wait(), timeout=_HANG_TIMEOUT)
    [wait] = [
        phase
        for phase in startup_trace._PHASES[THREAD_ID]
        if phase.name == "sandbox.await_git_identity"
    ]
    assert wait.end is None
    sandbox.release_identity.set()
    await asyncio.wait_for(startup, timeout=_HANG_TIMEOUT)

    assert wait.end is not None


async def test_identity_write_that_exits_non_zero_is_logged_and_commands_still_run(
    sandbox: _Sandbox, caplog: pytest.LogCaptureFixture
) -> None:
    sandbox.identity_exit_code = 255
    sandbox.release_identity.set()
    caplog.set_level(logging.WARNING)

    proxy = await asyncio.wait_for(ensure_sandbox_for_thread(THREAD_ID), timeout=_HANG_TIMEOUT)
    result = await asyncio.wait_for(proxy.aexecute("git status"), timeout=_HANG_TIMEOUT)

    assert result.exit_code == 0
    assert sandbox.ran == ["identity", "git status"]
    [failure] = [record for record in caplog.records if getattr(record, "exit_code", None) == 255]
    assert failure.levelno == logging.WARNING
    assert getattr(failure, "thread_id", None) == THREAD_ID
    assert getattr(failure, "sandbox_id", None) == sandbox.id


async def test_stalled_identity_write_does_not_hold_commands_indefinitely(
    sandbox: _Sandbox,
) -> None:
    sandbox.identity_stalls = True

    proxy = await asyncio.wait_for(ensure_sandbox_for_thread(THREAD_ID), timeout=_HANG_TIMEOUT)
    result = await asyncio.wait_for(proxy.aexecute("git status"), timeout=_HANG_TIMEOUT)

    assert result.exit_code == 0
    assert sandbox.ran == ["git status"]


async def test_identity_write_is_timed_in_apm_without_joining_a_later_startup_trace(
    sandbox: _Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed_after_release: dict[str, bool] = {}

    @contextmanager
    def apm_span(name: str, _metadata: dict[str, object]) -> Iterator[None]:
        yield
        closed_after_release[name] = sandbox.release_identity.is_set()

    monkeypatch.setattr(startup_trace, "_PHASES", {})
    monkeypatch.setattr(startup_trace, "_apm_span", apm_span)

    proxy = await asyncio.wait_for(ensure_sandbox_for_thread(THREAD_ID), timeout=_HANG_TIMEOUT)
    await asyncio.wait_for(sandbox.identity_started.wait(), timeout=_HANG_TIMEOUT)
    flush_phases(THREAD_ID)  # the run's prepare flushes while the write is still going
    sandbox.release_identity.set()
    await asyncio.wait_for(proxy.aexecute("git status"), timeout=_HANG_TIMEOUT)

    assert closed_after_release["agent.startup.sandbox.git_identity"]
    assert THREAD_ID not in startup_trace._PHASES
