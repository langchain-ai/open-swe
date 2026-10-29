"""The bot's git identity is written while the GitHub proxy is being configured.

The identity needs the sandbox, not the proxy, so running it after the proxy
chain put a full sandbox round trip on the critical path before the run's first
model call. Every connection to the box MDA opened does both.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.protocol import ExecuteResponse

from openswe.github.sandbox_access import SandboxGitHubAccess
from openswe.sandboxes.lifecycle import BoxSetup, _connect_existing_sandbox
from openswe.sandboxes.providers.langsmith import TimeoutLangSmithSandbox
from openswe.sandboxes.state import SandboxUnreachableError


async def _connect(backend: MagicMock, thread_id: str) -> object:
    return await _connect_existing_sandbox(thread_id, cached=backend, sandbox_id=backend.id)


def _backend(started: asyncio.Event) -> MagicMock:
    async def aexecute(_command: str) -> ExecuteResponse:
        started.set()
        return ExecuteResponse(output="ok", exit_code=0)

    return MagicMock(id="sandbox-mda", aexecute=AsyncMock(side_effect=aexecute))


@pytest.mark.asyncio
async def test_identity_is_written_while_the_proxy_is_configured() -> None:
    started = asyncio.Event()
    backend = _backend(started)

    async def configure(*_args: object, **_kwargs: object) -> None:
        # Serial ordering would leave the identity unstarted until this returns.
        await asyncio.wait_for(started.wait(), timeout=2)

    with (
        patch(
            "openswe.sandboxes.lifecycle.workspace_token",
            new_callable=AsyncMock,
            return_value=SandboxGitHubAccess("token"),
        ),
        patch("openswe.sandboxes.lifecycle.configure_sandbox_proxy", side_effect=configure),
        patch("openswe.sandboxes.lifecycle.record_proxy_token_expiry"),
    ):
        assert await _connect(backend, "thread-overlap") is backend

    backend.aexecute.assert_awaited_once()


@pytest.mark.asyncio
async def test_identity_failure_fails_the_sandbox() -> None:
    backend = MagicMock(
        id="sandbox-mda", aexecute=AsyncMock(side_effect=RuntimeError("identity failed"))
    )

    with (
        patch(
            "openswe.sandboxes.lifecycle.workspace_token",
            new_callable=AsyncMock,
            return_value=SandboxGitHubAccess("token"),
        ),
        patch("openswe.sandboxes.lifecycle.configure_sandbox_proxy", new_callable=AsyncMock),
        patch("openswe.sandboxes.lifecycle.record_proxy_token_expiry"),
        pytest.raises(RuntimeError, match="identity failed"),
    ):
        await _connect(backend, "thread-identity-fails")


@pytest.mark.asyncio
async def test_a_failed_proxy_does_not_leave_the_identity_write_running() -> None:
    release = asyncio.Event()

    async def aexecute(_command: str) -> str:
        await release.wait()
        return "ok"

    backend = MagicMock(id="sandbox-mda", aexecute=AsyncMock(side_effect=aexecute))

    with (
        patch(
            "openswe.sandboxes.lifecycle.workspace_token",
            new_callable=AsyncMock,
            return_value=SandboxGitHubAccess("token"),
        ),
        patch(
            "openswe.sandboxes.lifecycle.configure_sandbox_proxy",
            new_callable=AsyncMock,
            side_effect=RuntimeError("proxy failed"),
        ),
        patch("openswe.sandboxes.lifecycle.record_proxy_token_expiry"),
        pytest.raises(SandboxUnreachableError, match="proxy failed"),
    ):
        await _connect(backend, "thread-proxy-fails")

    # Cancelled with the sandbox, rather than left behind to report into a run
    # that has already given up on the box.
    assert not release.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("work_dir", ["/workspace", None])
async def test_box_setup_retries_failures_and_only_repeats_when_recreated(
    work_dir: str | None,
) -> None:
    sandbox = MagicMock(id="box-1")
    sandbox.name = "sandbox-mda"
    backend = TimeoutLangSmithSandbox(sandbox)
    output = "__open_swe_work_dir_ready__\n" if work_dir else ""
    execute = AsyncMock(
        return_value=ExecuteResponse(output=f"{output}could not lock config file", exit_code=1)
    )
    records: list[dict[str, object]] = []

    async def record(_thread_id: str, metadata: dict[str, object]) -> None:
        records.append(metadata)

    async def connect(setup: BoxSetup | None) -> None:
        await _connect_existing_sandbox(
            "thread-setup", cached=backend, sandbox_id="sandbox-mda", setup=setup
        )

    with (
        patch.object(backend, "aexecute", execute),
        patch("openswe.sandboxes.lifecycle.declared_work_dir", return_value=work_dir),
        patch(
            "openswe.sandboxes.lifecycle.workspace_token",
            new_callable=AsyncMock,
            return_value=SandboxGitHubAccess("token"),
        ),
        patch("openswe.sandboxes.lifecycle.configure_sandbox_proxy", new_callable=AsyncMock),
        patch("openswe.sandboxes.lifecycle.record_proxy_token_expiry"),
        patch("openswe.sandboxes.lifecycle.THREADS.update", side_effect=record),
    ):
        with pytest.raises(RuntimeError, match="could not lock config file"):
            await connect(None)
        assert records == []

        execute.return_value = ExecuteResponse(output=output, exit_code=0)
        await connect(None)
        setup = BoxSetup.recorded(records[-1])
        assert setup is not None
        await connect(setup)
        assert execute.await_count == 2

        sandbox.id = "box-2"
        await connect(setup)

    assert execute.await_count == 3
