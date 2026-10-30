"""Reset the fallback Git identity each run without a separate LangSmith command."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.protocol import ExecuteResponse
from runtime_api.loop import Prepare

from openswe.agent_api.graph import RunSession
from openswe.github.sandbox_access import SandboxGitHubAccess
from openswe.sandboxes.lifecycle import _connect_existing_sandbox
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.providers.langsmith import TimeoutLangSmithSandbox
from openswe.sandboxes.state import SANDBOX_BACKENDS, SandboxBackendProxy, SandboxUnreachableError
from openswe.sandboxes.tool_runtime import ToolSurface


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
async def test_identity_failure_does_not_fail_the_sandbox(caplog: pytest.LogCaptureFixture) -> None:
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
    ):
        assert await _connect(backend, "thread-identity-fails") is backend

    assert "Failed to write the bot Git identity" in caplog.text


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
    # that has already given up on the sandbox.
    assert not release.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("offloaded", [False, True])
async def test_identity_is_reset_each_run_in_the_first_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offloaded: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = TimeoutLangSmithSandbox(SimpleNamespace(name="sandbox-mda"))
    commands: list[str] = []
    home = tmp_path / "home"
    home.mkdir()

    async def execute(command: str, *, timeout: int | None = None) -> ExecuteResponse:
        commands.append(command)
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            env={**os.environ, "HOME": str(home)},
            cwd=home,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        return ExecuteResponse(output=output.decode(), exit_code=process.returncode)

    monkeypatch.setattr(backend, "_aexecute_once", execute)
    monkeypatch.setattr("openswe.sandboxes.providers.langsmith.WORKSPACE_DIR", str(tmp_path))

    async def run(command: str) -> ExecuteResponse:
        if not offloaded:
            return await backend.aexecute(command)
        result = await backend.aexecute_with_offload(
            command, str(tmp_path / "capture.txt"), max_inline_bytes=10000
        )
        assert result.response is not None
        return result.response

    with (
        patch(
            "openswe.sandboxes.lifecycle.workspace_token",
            new_callable=AsyncMock,
            return_value=SandboxGitHubAccess("token"),
        ),
        patch("openswe.sandboxes.lifecycle.configure_sandbox_proxy", new_callable=AsyncMock),
        patch("openswe.sandboxes.lifecycle.record_proxy_token_expiry"),
    ):
        await _connect_existing_sandbox("thread-reset", cached=backend, sandbox_id=backend.id)
        assert commands == []
        result = await run("git config --global user.email")
        assert result.exit_code == 0
        assert result.output.strip() == "open-swe@users.noreply.github.com"
        assert len(commands) == 1
        await backend.ensure_git_identity()
        assert len(commands) == 1
        await backend.aexecute("git config --global user.email wrong@example.com")
        await _connect_existing_sandbox("thread-reset", cached=backend, sandbox_id=backend.id)
        result = await run("git config --global user.email")
        assert result.output.strip() == "open-swe@users.noreply.github.com"
        assert len(commands) == 3
        await _connect_existing_sandbox("thread-reset", cached=backend, sandbox_id=backend.id)
        failed = await backend.aexecute("printf user-output; exit 7")
        assert failed.output == "user-output"
        assert failed.exit_code == 7
        result = await run("printf next-command")
        assert result.output == "next-command"

        await _connect_existing_sandbox("thread-reset", cached=backend, sandbox_id=backend.id)
        (home / ".gitconfig.lock").touch()
        target = tmp_path / "command-ran"
        result = await run(f"touch {target}")
        assert result.exit_code == 0
        assert target.exists()
        assert "Failed to write the bot Git identity" in caplog.text
        assert "could not lock config file" not in result.output
        assert (await run("printf still-usable")).output == "still-usable"

        session = RunSession(
            ToolSurface(), {"configurable": {"thread_id": "thread-reset"}}, None, started_here=True
        )
        monkeypatch.setitem(
            SANDBOX_BACKENDS, "thread-reset", SandboxBackendProxy(backend, thread_id="thread-reset")
        )
        monkeypatch.setattr(session, "bind_sandbox", AsyncMock())
        backend.reset_git_identity()
        await session.prepare(Prepare())

        (home / ".gitconfig.lock").unlink()
        await _connect_existing_sandbox("thread-reset", cached=backend, sandbox_id=backend.id)
        result = await run("git config --global user.email")
        assert result.output.strip() == "open-swe@users.noreply.github.com"

        unavailable = tmp_path / "not-a-directory"
        unavailable.touch()
        monkeypatch.setattr("openswe.sandboxes.providers.langsmith.WORKSPACE_DIR", str(unavailable))
        monkeypatch.setattr("openswe.sandboxes.paths.WORKSPACE_DIR", str(unavailable))
        backend.reset_git_identity()
        assert await resolve_sandbox_work_dir(backend) == str(home)
        assert (await run("printf usable-without-workspace")).output == "usable-without-workspace"
        unavailable.unlink()
        backend.reset_git_identity()
        assert await resolve_sandbox_work_dir(backend) == str(unavailable)
