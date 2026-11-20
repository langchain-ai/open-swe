"""Reset the fallback Git identity each run without a separate LangSmith command."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from deepagents.backends.protocol import ExecuteResponse
from runtime_api.sandbox_setup import GitIdentity

from openswe.agent_api.run_sandbox import RunSandbox
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.providers.langsmith import TimeoutLangSmithSandbox
from openswe.sandboxes.state import SANDBOX_BACKENDS, SandboxBackendProxy


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

    backend.reset_git_identity(GitIdentity())
    assert commands == []
    result = await run("git config --global user.email")
    assert result.exit_code == 0
    assert result.output.strip() == "open-swe@users.noreply.github.com"
    assert len(commands) == 1
    await backend.ensure_git_identity()
    assert len(commands) == 1
    await backend.aexecute("git config --global user.email wrong@example.com")
    backend.reset_git_identity(GitIdentity())
    result = await run("git config --global user.email")
    assert result.output.strip() == "open-swe@users.noreply.github.com"
    assert len(commands) == 3
    backend.reset_git_identity(GitIdentity())
    failed = await backend.aexecute("printf user-output; exit 7")
    assert failed.output == "user-output"
    assert failed.exit_code == 7
    result = await run("printf next-command")
    assert result.output == "next-command"

    backend.reset_git_identity(GitIdentity())
    (home / ".gitconfig.lock").touch()
    target = tmp_path / "command-ran"
    result = await run(f"touch {target}")
    assert result.exit_code == 0
    assert target.exists()
    assert "Failed to write the bot Git identity" in caplog.text
    assert "could not lock config file" not in result.output
    assert (await run("printf still-usable")).output == "still-usable"

    monkeypatch.setitem(
        SANDBOX_BACKENDS, "thread-reset", SandboxBackendProxy(backend, thread_id="thread-reset")
    )
    backend.reset_git_identity(GitIdentity())
    await RunSandbox(
        {"configurable": {"thread_id": "thread-reset"}}, reviews=False
    ).finish_git_identity()

    (home / ".gitconfig.lock").unlink()
    backend.reset_git_identity(GitIdentity())
    result = await run("git config --global user.email")
    assert result.output.strip() == "open-swe@users.noreply.github.com"

    owner = GitIdentity(name="Alice", email="alice@users.noreply.github.com")
    backend.reset_git_identity(owner)
    result = await run("git config --global user.email")
    assert result.output.strip() == "alice@users.noreply.github.com"

    unavailable = tmp_path / "not-a-directory"
    unavailable.touch()
    monkeypatch.setattr("openswe.sandboxes.providers.langsmith.WORKSPACE_DIR", str(unavailable))
    monkeypatch.setattr("openswe.sandboxes.paths.WORKSPACE_DIR", str(unavailable))
    backend.reset_git_identity(GitIdentity())
    assert await resolve_sandbox_work_dir(backend) == str(home)
    assert (await run("printf usable-without-workspace")).output == "usable-without-workspace"
    unavailable.unlink()
    backend.reset_git_identity(GitIdentity())
    assert await resolve_sandbox_work_dir(backend) == str(unavailable)
