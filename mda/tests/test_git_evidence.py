import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from rt.git_evidence import GitCheckout, GitSnapshot, TurnGit
from rt.sandbox_script import SandboxScript, SandboxScriptError
from runtime_api.operations import PullRequestHead


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _checkout(root: Path, name: str, remote: str, branch: str = "work") -> Path:
    path = root / name
    _git("init", "-b", branch, str(path))
    for key, value in [("user.name", "Test"), ("user.email", "test@example.com")]:
        _git("-C", str(path), "config", key, value)
    _git("-C", str(path), "remote", "add", "origin", remote)
    (path / "file").write_text("initial")
    _git("-C", str(path), "add", "file")
    _git("-C", str(path), "commit", "-m", "initial")
    return path


@pytest.fixture
def sandbox() -> LocalShellBackend:
    return LocalShellBackend(virtual_mode=False)


async def test_the_work_order_names_the_turns_repository_and_a_pull_request_its_branch(
    tmp_path: Path, sandbox: LocalShellBackend
) -> None:
    actual = _checkout(tmp_path, "actual", "git@github.com:org/actual.git", "local")
    later = _checkout(tmp_path, "later", "https://github.com/org/later.git")
    git = TurnGit()
    git.start(sandbox, str(tmp_path))

    # A tool changes one checkout; the next model call fingerprints it.
    await git.changing()
    (actual / "file").write_text("changed")
    git.changed(10)
    git.settle()
    await git.metadata()
    # Then another, and a pull request is opened for the first one's work.
    await git.changing()
    _git("-C", str(later), "checkout", "-b", "later")
    git.changed(20)
    git.found_pull_requests([PullRequestHead(repo="org/actual", branch="Exact/PR-head")], 30)

    assert await git.metadata() == {
        "open_swe_git_targets": [
            {"repo": "org/actual", "branch": "Exact/PR-head"},
            {"repo": "org/later", "branch": "later"},
        ],
        "ls_git_repo": "org/actual",
        "ls_git_branch": "Exact/PR-head",
    }


async def test_an_edited_file_is_relative_to_its_actual_checkout(
    tmp_path: Path, sandbox: LocalShellBackend
) -> None:
    repo = _checkout(tmp_path, "repo", "git@github.com:org/actual.git")
    script = SandboxScript("edited_file.py")
    assert await script.run(sandbox, {"path": str(repo / "file")}) == {
        "repository": "org/actual",
        "branch": "work",
        "path": "file",
    }
    (repo / ".gitignore").write_text("ignored\n")
    (repo / "ignored").write_text("generated")
    assert await script.run(sandbox, {"path": str(repo / "ignored")}) == {}
    assert await script.run(sandbox, {"path": str(repo / ".git" / "config")}) == {}
    outside = tmp_path / "outside"
    outside.write_text("not tracked")
    (repo / "link").symlink_to(outside)
    assert await script.run(sandbox, {"path": str(repo / "link")}) == {}


async def test_a_turn_that_changed_nothing_waits_for_no_fingerprint(
    tmp_path: Path, sandbox: LocalShellBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    async def never(*args: object, **kwargs: object) -> dict[str, object]:
        started.set()
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(SandboxScript, "run", never)
    git = TurnGit()
    git.start(sandbox, str(tmp_path))
    await started.wait()

    assert await asyncio.wait_for(git.metadata(), 1) == {}


async def test_a_sandbox_the_script_cannot_read_leaves_only_the_pull_requests(
    tmp_path: Path, sandbox: LocalShellBackend
) -> None:
    git = TurnGit()
    git.start(sandbox, str(tmp_path / "missing"))
    await git.changing()
    git.changed(1)
    git.found_pull_requests([PullRequestHead(repo="org/actual", branch="Exact/head")], 2)

    assert await git.metadata() == {
        "open_swe_git_targets": [{"repo": "org/actual", "branch": "Exact/head"}],
        "ls_git_repo": "org/actual",
        "ls_git_branch": "Exact/head",
    }


async def test_the_script_sees_more_edits_to_an_already_dirty_checkout(
    tmp_path: Path, sandbox: LocalShellBackend
) -> None:
    for name in ("a-no-origin", "b-unborn"):
        _git("init", str(tmp_path / name))
    _git("-C", str(tmp_path / "b-unborn"), "remote", "add", "origin", "https://github.com/o/u.git")
    repo = _checkout(tmp_path, "repo", "git@github.com:org/actual.git")
    script = SandboxScript("git_trace.py")

    async def fingerprint() -> GitSnapshot:
        return GitSnapshot.model_validate(await script.run(sandbox, {"root": str(tmp_path)}))

    (repo / "file").write_text("dirty")
    first = (await fingerprint()).repos
    (repo / "file").write_text("different dirty content")
    second = (await fingerprint()).repos

    assert list(first) == list(second) == [str(repo)]
    assert first[str(repo)].repo == "org/actual"
    assert first[str(repo)].branch == "work"
    assert first[str(repo)].dirty != second[str(repo)].dirty

    broken = tmp_path / "broken"
    shutil.copytree(repo, broken)
    (broken / ".git" / "index").write_bytes(b"corrupt")
    before = await fingerprint()
    assert list(before.repos) == [str(repo)]
    assert list(before.skipped) == [str(broken)]
    recovered = GitSnapshot(repos={str(broken): GitCheckout(repo="org/actual", branch="work")})
    assert recovered.changes_since(before) == []
    with pytest.raises(SandboxScriptError, match="Could not inspect workspace git state"):
        await script.run(sandbox, {"root": str(tmp_path / "missing")})
