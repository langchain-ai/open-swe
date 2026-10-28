import asyncio
import os
import shlex
import shutil
from pathlib import Path
from typing import cast

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from openswe.sandboxes.repo_prep import materialize_trusted_skills, prepare_review_repo


class _ShellBackend:
    def __init__(self) -> None:
        self.processes: list[asyncio.subprocess.Process] = []

    async def aexecute(self, command: str, *, timeout: int = 10) -> ExecuteResponse:
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


@pytest.mark.parametrize("cancel_old", [False, True])
async def test_overlapping_checkouts_leave_the_newest_head_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_old: bool
) -> None:
    shell = _ShellBackend()
    source = tmp_path / "source"
    setup = await shell.aexecute(
        f"git init -q {shlex.quote(str(source))} && cd {shlex.quote(str(source))} && "
        "git -c user.name=Test -c user.email=test@example.com commit -qm old --allow-empty && "
        "git rev-parse HEAD && "
        "git -c user.name=Test -c user.email=test@example.com commit -qm new --allow-empty && "
        "git rev-parse HEAD"
    )
    assert setup.exit_code == 0, setup.output
    old, new = setup.output.splitlines()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_git = shutil.which("git")
    assert real_git is not None
    git = bin_dir / "git"
    git.write_text(
        '#!/bin/sh\nif [ "$1" = checkout ] && [ "$3" = "$OLD_HEAD" ]; then\n'
        '  touch "$BARRIER/started"\n'
        '  while [ ! -f "$BARRIER/release" ]; do sleep 0.01; done\n'
        "fi\n"
        f'exec {shlex.quote(real_git)} "$@"\n'
    )
    git.chmod(0o755)
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\nexec git clone -q --no-checkout "$SOURCE" "${3##*/}"\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("SOURCE", str(source))
    monkeypatch.setenv("BARRIER", str(tmp_path))
    monkeypatch.setenv("OLD_HEAD", old)

    async def prepare(head: str) -> bool:
        return await prepare_review_repo(
            cast(SandboxBackendProtocol, shell),
            work_dir=str(tmp_path / "work"),
            repo_owner="acme",
            repo_name="widget",
            head_sha=head,
        )

    first = asyncio.create_task(prepare(old))
    second: asyncio.Task[bool] | None = None
    try:
        async with asyncio.timeout(5):
            while not (tmp_path / "started").exists():
                await asyncio.sleep(0.01)
        if cancel_old:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        second = asyncio.create_task(prepare(new))
        await asyncio.sleep(0.05)
        assert not second.done()
    finally:
        (tmp_path / "release").touch()
        if not first.cancelled():
            assert await asyncio.wait_for(first, 5) is True
        if second is not None:
            assert await asyncio.wait_for(second, 5) is True
        for process in shell.processes:
            await asyncio.wait_for(process.wait(), 5)
    result = await shell.aexecute(
        f"git -C {shlex.quote(str(tmp_path / 'work/widget'))} rev-parse HEAD"
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == new


@pytest.mark.parametrize("process_status", [0, 1, 2, 127])
async def test_prepare_cleans_stale_locks_only_without_git_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_status: int
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pgrep = bin_dir / "pgrep"
    pgrep.write_text(f"#!/bin/sh\nexit {process_status}\n")
    pgrep.chmod(0o755)
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = fetch ]; then\n'
        "  if [ -f .git/index.lock ] || [ -f .git/shallow.lock ]; then\n"
        "    echo locks-present >> fetches\n"
        "  else\n"
        "    echo locks-absent >> fetches\n"
        "  fi\n"
        "fi\n"
    )
    git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    repo_dir = tmp_path / "widget"
    (repo_dir / ".git").mkdir(parents=True)
    for name in ("index.lock", "shallow.lock"):
        (repo_dir / ".git" / name).touch()
    assert (
        await prepare_review_repo(
            cast(SandboxBackendProtocol, _ShellBackend()),
            work_dir=str(tmp_path),
            repo_owner="acme",
            repo_name="widget",
            head_sha="",
            base_sha="base",
        )
        is True
    )
    expected = "locks-absent" if process_status == 1 else "locks-present"
    assert (repo_dir / "fetches").read_text().splitlines() == [expected, expected]
    for name in ("index.lock", "shallow.lock"):
        assert (repo_dir / ".git" / name).exists() is (process_status != 1)
