import asyncio
import os
from pathlib import Path
from typing import cast

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from openswe.sandboxes.repo_prep import materialize_trusted_skills, prepare_review_repo


class _FakeSandboxBackend:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        raise_exc: bool = False,
        output: str = "",
        outputs: list[str] | None = None,
    ) -> None:
        self._exit_code = exit_code
        self._raise = raise_exc
        self._output = output
        self._outputs = outputs
        self.commands: list[str] = []

    @property
    def id(self) -> str:
        return "fake-sandbox"

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        del timeout
        if self._raise:
            raise RuntimeError("sandbox unreachable")
        self.commands.append(command)
        output = self._output
        if self._outputs is not None:
            output = self._outputs[len(self.commands) - 1]
        return ExecuteResponse(output=output, exit_code=self._exit_code, truncated=False)


async def test_prepare_review_repo_clones_and_checks_out_head() -> None:
    backend = _FakeSandboxBackend()
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="abc123",
        pr_number=42,
        base_sha="def456",
    )
    assert ok is True
    assert len(backend.commands) == 1
    cmd = backend.commands[0]
    assert "gh repo clone acme/widget" in cmd
    assert "/work/widget/.git" in cmd
    assert "git fetch origin def456" in cmd
    assert "git fetch origin refs/pull/42/head" in cmd
    assert "git checkout --force abc123 --quiet" in cmd
    assert "git checkout --force abc123 --quiet 2>/dev/null || true" not in cmd
    assert '[ "$(git rev-parse HEAD)" = abc123 ]' in cmd
    assert "git fetch --all --quiet || true" in cmd


async def test_prepare_review_repo_returns_false_on_nonzero_exit() -> None:
    backend = _FakeSandboxBackend(exit_code=1)
    ok = await prepare_review_repo(
        cast(SandboxBackendProtocol, backend),
        work_dir="/work",
        repo_owner="acme",
        repo_name="widget",
        head_sha="abc",
    )
    assert ok is False


async def test_materialize_trusted_skills_extracts_from_trusted_ref() -> None:
    backend = _FakeSandboxBackend(outputs=["/work/.review-skills/.agents/skills\n", ""])
    sources = await materialize_trusted_skills(
        cast(SandboxBackendProtocol, backend), repo_dir="/work/widget", trusted_ref="def456"
    )
    assert sources == ["/work/.review-skills/.agents/skills/"]
    assert len(backend.commands) == 2
    for cmd in backend.commands:
        assert "git cat-file -e def456:" in cmd
        assert "git archive def456" in cmd


@pytest.mark.parametrize("cancel_prepare", [False, True])
async def test_prepare_serializes_repositories_with_a_bounded_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_prepare: bool
) -> None:
    import openswe.sandboxes.repo_prep as prep

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\nname=${3##*/}\n"
        'touch "$name.started"\n'
        'while [ "$name" = widget ] && [ ! -f release ]; do sleep 0.01; done\n'
        'mkdir -p "$name/.git"\n'
    )
    gh.chmod(0o755)
    git = bin_dir / "git"
    git.write_text("#!/bin/sh\ntouch fetched\n")
    git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(prep, "PREP_LOCK_TIMEOUT_SECONDS", 0.1)

    class ShellBackend:
        def __init__(self) -> None:
            self.processes: list[asyncio.subprocess.Process] = []

        async def aexecute(self, command: str, *, timeout: int) -> ExecuteResponse:
            process = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self.processes.append(process)
            output, _ = await asyncio.wait_for(process.communicate(), timeout)
            return ExecuteResponse(output=output.decode(), exit_code=process.returncode)

    shell = ShellBackend()
    backend = cast(SandboxBackendProtocol, shell)

    async def prepare(name: str) -> None:
        await prep.prepare_review_repo_or_raise(
            backend, work_dir=str(tmp_path), repo_owner="acme", repo_name=name, head_sha=""
        )

    first = asyncio.create_task(prepare("widget"))
    try:
        async with asyncio.timeout(5):
            while not (tmp_path / "widget.started").exists():
                await asyncio.sleep(0.01)
        if cancel_prepare:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        await asyncio.wait_for(prepare("other"), 5)
        with pytest.raises(prep.ReviewRepoPreparationError, match="Timed out waiting"):
            await asyncio.wait_for(prepare("widget"), 5)
        assert not (tmp_path / "widget" / "fetched").exists()
    finally:
        (tmp_path / "release").touch()
        if not first.cancelled():
            await asyncio.wait_for(first, 5)
        for process in shell.processes:
            await asyncio.wait_for(process.wait(), 5)
    await prepare("widget")
    assert (tmp_path / "widget" / "fetched").exists()


@pytest.mark.parametrize("raises", [False, True])
async def test_preparation_failure_preserves_boolean_api_and_bounded_diagnostics(
    raises: bool,
) -> None:
    from openswe.sandboxes.repo_prep import ReviewRepoPreparationError, prepare_review_repo_or_raise

    backend = cast(
        SandboxBackendProtocol,
        _FakeSandboxBackend(
            exit_code=1, output="x" * 3000 + "fatal: checkout failed", raise_exc=raises
        ),
    )
    kwargs = {"work_dir": "/work", "repo_owner": "acme", "repo_name": "widget", "head_sha": "abc"}
    assert await prepare_review_repo(backend, **kwargs) is False
    with pytest.raises(ReviewRepoPreparationError) as error:
        await prepare_review_repo_or_raise(backend, **kwargs)
    assert len(str(error.value)) <= 2000
    assert str(error.value).endswith("sandbox unreachable" if raises else "fatal: checkout failed")
