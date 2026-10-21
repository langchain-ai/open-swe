"""Exercise actual Git objects and refs without network access or credentials."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid7

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from openswe.merge_queue.git import (
    CanaryLanding,
    CandidateConflict,
    CandidateGit,
    PullHead,
    candidate_ref,
)


class RepositoryFixture:
    def __init__(self, path: Path):
        self.path = path
        self.sandbox = Mock(spec=SandboxBackendProtocol)
        self.sandbox.aexecute = AsyncMock(side_effect=self.execute)

    async def execute(self, command: str, *, timeout: int) -> ExecuteResponse:
        """Run the sandbox command contract against temporary test repositories."""
        process = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            command,
            cwd=self.path.parent,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(timeout):
                stdout, stderr = await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return ExecuteResponse(output=(stdout + stderr).decode(), exit_code=process.returncode)

    async def git(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            *args,
            cwd=self.path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        return stdout.decode().strip()

    async def commit(self, name: str, content: str) -> str:
        (self.path / name).write_text(content)
        await self.git("add", name)
        await self.git("commit", "-m", name)
        return await self.git("rev-parse", "HEAD")


@pytest.fixture
async def repository(tmp_path: Path) -> RepositoryFixture:
    path = tmp_path / "repository with 'quotes'"
    path.mkdir()
    repo = RepositoryFixture(path)
    await repo.git("init", "--initial-branch=main", "--template=")
    await repo.commit("README", "base\n")
    return repo


@pytest.mark.parametrize("size", [1, 3])
async def test_ordered_batch_is_reproducible_and_lands_the_exact_tip(
    repository: RepositoryFixture, tmp_path: Path, size: int
) -> None:
    base = await repository.git("rev-parse", "main")
    pulls = []
    for number in range(1, size + 1):
        await repository.git("checkout", "--detach", base)
        pulls.append(
            PullHead(number=number, sha=await repository.commit(f"file{number}", f"PR {number}\n"))
        )
    git = CanaryLanding(
        repository.sandbox,
        tmp_path / "candidate",
        str(repository.path),
        canary_remote=str(repository.path),
    )
    await git.initialize()
    candidate = await git.build("main", base, tuple(pulls), timestamp=1_700_000_000)
    assert await repository.git("rev-parse", "main") == base
    rebuilt = CandidateGit(repository.sandbox, tmp_path / "rebuilt", str(repository.path))
    await rebuilt.initialize()
    assert await rebuilt.build("main", base, tuple(pulls), timestamp=1_700_000_000) == candidate
    batch_id = uuid7()
    await git.publish(candidate, batch_id)
    await git.publish(candidate, batch_id)
    assert await repository.git("rev-parse", candidate_ref(batch_id)) == candidate.sha
    previous = base
    for sha, pull in zip(candidate.merges, pulls, strict=True):
        assert (await repository.git("rev-list", "--parents", "-n", "1", sha)).split() == [
            sha,
            previous,
            pull.sha,
        ]
        previous = sha
    assert await git.promote(candidate) == "landed"
    assert await git.promote(candidate) == "landed"
    assert await repository.git("rev-parse", "main") == candidate.sha
    assert await repository.git("rev-parse", "main^{tree}") == candidate.tree_sha
    with pytest.raises(ValueError, match="ref changed"):
        await git.delete(batch_id, base)
    await git.delete(batch_id, candidate.sha)
    await git.delete(batch_id, candidate.sha)
    assert await git.remote_sha(candidate_ref(batch_id)) is None


async def test_stale_base_and_replaced_candidate_refs_are_never_overwritten(
    repository: RepositoryFixture, tmp_path: Path
) -> None:
    base = await repository.git("rev-parse", "main")
    await repository.git("checkout", "--detach", base)
    head = await repository.commit("feature", "feature\n")
    git = CanaryLanding(
        repository.sandbox,
        tmp_path / "candidate",
        str(repository.path),
        canary_remote=str(repository.path),
    )
    await git.initialize()
    candidate = await git.build(
        "main", base, (PullHead(number=1, sha=head),), timestamp=1_700_000_000
    )
    await repository.git("checkout", "--detach", base)
    advanced = await repository.commit("other", "other\n")
    await repository.git("update-ref", "refs/heads/main", advanced)
    assert await git.promote(candidate) == "stale"
    assert await repository.git("rev-parse", "main") == advanced
    batch_id = uuid7()
    await repository.git("update-ref", candidate_ref(batch_id), advanced)
    with pytest.raises(ValueError, match="another SHA"):
        await git.publish(candidate, batch_id)
    with pytest.raises(ValueError, match="ref changed"):
        await git.delete(batch_id, candidate.sha)
    assert await repository.git("rev-parse", candidate_ref(batch_id)) == advanced


async def test_conflicts_and_git_configuration_cannot_execute_candidate_code(
    repository: RepositoryFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = await repository.git("rev-parse", "main")
    await repository.git("checkout", "--detach", base)
    first = await repository.commit("README", "one\n")
    await repository.git("checkout", "--detach", base)
    await repository.commit(".gitattributes", "README merge=evil\n")
    second = await repository.commit("README", "two\n")
    sentinel = tmp_path / "executed"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "merge.evil.driver")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"touch {sentinel}")
    git = CandidateGit(repository.sandbox, tmp_path / "candidate", str(repository.path))
    await git.initialize()
    with pytest.raises(CandidateConflict, match="PR #2") as conflict:
        await git.build(
            "main",
            base,
            (PullHead(number=1, sha=first), PullHead(number=2, sha=second)),
            timestamp=1_700_000_000,
        )
    assert conflict.value.number == 2
    assert not sentinel.exists()
    (Path(git.directory) / "config").write_text('[merge "evil"]\n driver = malicious\n')
    with pytest.raises(ValueError, match="configuration"):
        await git.remote_sha("refs/heads/main")


@pytest.mark.parametrize("exit_code,truncated", [(None, False), (0, True)])
async def test_incomplete_sandbox_results_cannot_confirm_a_candidate_ref(
    tmp_path: Path, exit_code: int | None, truncated: bool
) -> None:
    sandbox = Mock(spec=SandboxBackendProtocol)
    sandbox.aexecute = AsyncMock(
        return_value=ExecuteResponse(
            output=f"{'a' * 40}\trefs/heads/main\n", exit_code=exit_code, truncated=truncated
        )
    )
    git = CandidateGit(sandbox, tmp_path / "candidate", "https://github.com/acme/widgets.git")
    with pytest.raises(RuntimeError, match="incomplete"):
        await git.remote_sha("refs/heads/main")
