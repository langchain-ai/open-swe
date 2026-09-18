"""Capturing a turn's checkpoint in a real repository.

The capture is one shell script, so a double would only prove the script is the
string we wrote. Run it against a temporary repository instead: what matters is
that it records the working tree and leaves HEAD, the index and the tree alone.
"""

import subprocess
from pathlib import Path
from typing import cast
from uuid import UUID, uuid7

import pytest
from deepagents.backends.protocol import ExecuteResponse

from agent.sandboxes.state import SandboxBackendProxy
from agent.transcript import checkpoints

THREAD_ID = str(uuid7())


class _LocalSandbox:
    """A sandbox whose "sandbox" is a directory on this machine."""

    has_backend = True

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        del timeout
        completed = subprocess.run(
            ["bash", "-c", command], cwd=self.cwd, capture_output=True, text=True, check=False
        )
        return ExecuteResponse(
            output=completed.stdout + completed.stderr, exit_code=completed.returncode
        )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
    ):
        _git(path, *args)
    (path / "seed.txt").write_text("one\n")
    _git(path, "add", "seed.txt")
    _git(path, "commit", "-qm", "seed")
    return path


@pytest.fixture
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = _repo(tmp_path / "repo")
    monkeypatch.setitem(
        checkpoints.SANDBOX_BACKENDS, THREAD_ID, cast(SandboxBackendProxy, _LocalSandbox(repo))
    )

    async def _turn_context(thread_id: str, turn_id: UUID) -> tuple[int, str | None, str | None]:
        del thread_id, turn_id
        return 1, None, None

    monkeypatch.setattr(checkpoints, "_turn_context", _turn_context)
    return repo


async def test_a_checkpoint_records_the_worktree_and_changes_nothing(_sandbox: Path) -> None:
    repo = _sandbox
    start_head = _git(repo, "rev-parse", "HEAD")
    (repo / "seed.txt").write_text("two\n")
    (repo / "added.py").write_text("print('hi')\n")
    status_before = _git(repo, "status", "--porcelain")

    command = await checkpoints.checkpoint_command(
        THREAD_ID, uuid7(), run_id=None, start_head=start_head
    )
    event = command.event
    assert event.type == "turn.checkpoint.completed"

    assert event.status == "ready"
    assert event.commit
    assert event.checkpoint_ref == f"refs/open-swe/checkpoints/{THREAD_ID}/turn/1"
    # Untracked-but-not-ignored work is part of the turn, so it is checkpointed.
    assert {(file.path, file.status) for file in event.files} == {
        ("seed.txt", "modified"),
        ("added.py", "added"),
    }

    assert _git(repo, "rev-parse", event.checkpoint_ref) == event.commit
    assert _git(repo, "rev-parse", "HEAD") == start_head
    assert _git(repo, "status", "--porcelain") == status_before
