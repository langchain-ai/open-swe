import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends import LocalShellBackend

from agent.sandboxes import handoff


def _git(cwd: Path, script: str) -> str:
    return subprocess.run(
        ["bash", "-c", script], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _backend(root: Path, sandbox_id: str) -> LocalShellBackend:
    backend = LocalShellBackend(root, virtual_mode=False, inherit_env=True)
    backend._sandbox_id = sandbox_id
    return backend


async def test_handoff_carries_unpushed_commits_and_uncommitted_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    identity = "-c user.name=t -c user.email=t@example.com"
    _git(tmp_path, "git init -q --bare -b main origin.git && git clone -q origin.git seed")
    _git(tmp_path / "seed", f"echo a>a && echo d>d && git add . && git {identity} commit -qm base")
    _git(tmp_path / "seed", "git push -q origin HEAD:main")
    (tmp_path / "cloud").mkdir()
    _git(tmp_path / "cloud", f"git clone -q {tmp_path}/origin.git repo")
    source = tmp_path / "cloud" / "repo"
    _git(source, f"git checkout -qb feat && echo b>b && git add b && git {identity} commit -qm b")
    _git(source, "echo edit>>a && rm d && echo new>untracked")
    _git(tmp_path, "git clone -q origin.git mac && git -C mac worktree add -q -b local ../wt main")
    threads = MagicMock(update=AsyncMock())
    monkeypatch.setattr(handoff, "get_client", lambda: MagicMock(threads=threads))
    monkeypatch.setattr(
        handoff, "connect_sandbox", AsyncMock(return_value=_backend(tmp_path / "cloud", "cloud"))
    )

    await handoff.complete_handoff(
        "thread",
        {handoff.HANDOFF_FROM_KEY: "cloud", "repo": {"owner": "o", "name": "repo"}},
        _backend(tmp_path / "wt", "bridge:mac"),
    )

    target = tmp_path / "wt"
    assert _git(target, "git branch --show-current").strip() == "feat"
    assert _git(target, "git status --short").splitlines() == [" M a", " D d", "?? untracked"]
    assert _git(source, "git status --short").splitlines() == [" M a", " D d", "?? untracked"]
    threads.update.assert_awaited_once_with(
        thread_id="thread", metadata={handoff.HANDOFF_FROM_KEY: None}
    )
