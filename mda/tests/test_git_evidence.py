import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from rt.git_evidence import GitCheckout, GitSnapshot, TurnGit
from rt.sandbox_script import SandboxScript, SandboxScriptError
from runtime_api.operations import PullRequestHead


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


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
    await git.metadata()
    first_head = _git("-C", str(later), "rev-parse", "HEAD")
    await git.changing()
    (later / "file").write_text("committed")
    _git("-C", str(later), "add", "file")
    _git("-C", str(later), "commit", "-m", "next revision")
    git.changed(40)

    assert await git.metadata() == {
        "open_swe_git_targets": [
            {"repo": "org/actual", "branch": "Exact/PR-head"},
            {"repo": "org/later", "branch": "later"},
        ],
        "open_swe_git_commits": [
            {
                "repo": "org/actual",
                "branch": "local",
                "commit": _git("-C", str(actual), "rev-parse", "HEAD"),
            },
            {"repo": "org/later", "branch": "later", "commit": first_head},
            {
                "repo": "org/later",
                "branch": "later",
                "commit": _git("-C", str(later), "rev-parse", "HEAD"),
            },
        ],
        "ls_git_repo": "org/actual",
        "ls_git_branch": "Exact/PR-head",
    }


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
        "open_swe_git_incomplete": True,
    }


async def test_failing_fingerprints_back_off_then_stop_for_the_turn(
    tmp_path: Path, sandbox: LocalShellBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def baseline_then_timeouts(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append(len(calls))
        if len(calls) == 1:
            return {"repos": {}}
        return {"repos": {}, "skipped": {str(tmp_path / "repo"): "git status timed out"}}

    monkeypatch.setattr(SandboxScript, "run", baseline_then_timeouts)
    monkeypatch.setattr(TurnGit, "RETRY_AFTER", (0.0, 3600.0))
    git = TurnGit()
    git.start(sandbox, str(tmp_path))
    await git.changing()

    for at in range(1, 4):
        git.changed(at)
        git.settle()
        for _ in range(5):
            await asyncio.sleep(0)
    # The first failure is retried at once, the second not within the hour.
    assert len(calls) == 3
    # The turn's end still fingerprints its last change, a third failure, and says so.
    assert await git.metadata() == {"open_swe_git_incomplete": True}
    assert len(calls) == 4

    monkeypatch.setattr(TurnGit, "RETRY_AFTER", (0.0, 0.0))
    git.changed(5)
    git.settle()
    assert await git.metadata() == {"open_swe_git_incomplete": True}
    assert len(calls) == 4


async def test_a_slow_baseline_lets_the_tool_run_and_marks_the_evidence_incomplete(
    tmp_path: Path, sandbox: LocalShellBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    scanned = asyncio.Event()
    changed: dict[str, object] = {
        "repos": {str(tmp_path / "repo"): {"repo": "org/actual", "branch": "work"}}
    }

    async def slow_baseline(*args: object, **kwargs: object) -> dict[str, object]:
        if not scanned.is_set():
            await scanned.wait()
            return {"repos": {}}
        return changed

    monkeypatch.setattr(SandboxScript, "run", slow_baseline)
    monkeypatch.setattr(TurnGit, "BASELINE_WAIT", 0.01)
    git = TurnGit()
    git.start(sandbox, str(tmp_path))

    await asyncio.wait_for(git.changing(), 1)
    git.changed(1)
    scanned.set()

    assert await git.metadata() == {
        "open_swe_git_targets": [{"repo": "org/actual", "branch": "work"}],
        "ls_git_repo": "org/actual",
        "ls_git_branch": "work",
        "open_swe_git_incomplete": True,
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


async def test_the_script_sees_untracked_staged_deleted_and_renamed_work(
    tmp_path: Path, sandbox: LocalShellBackend
) -> None:
    repo = _checkout(tmp_path, "repo", "https://github.com/org/actual.git")
    script = SandboxScript("git_trace.py")

    async def dirty() -> str:
        snapshot = GitSnapshot.model_validate(await script.run(sandbox, {"root": str(tmp_path)}))
        return snapshot.repos[str(repo)].dirty

    seen = [await dirty()]
    (repo / "new").mkdir()
    (repo / "new" / "untracked").write_text("one")
    seen.append(await dirty())
    (repo / "new" / "untracked").write_text("one more")
    seen.append(await dirty())
    _git("-C", str(repo), "add", "new/untracked")
    seen.append(await dirty())
    _git("-C", str(repo), "mv", "file", "moved")
    seen.append(await dirty())
    (repo / "moved").unlink()
    seen.append(await dirty())

    assert len(set(seen)) == len(seen)
    assert seen[-1] == await dirty()


async def test_the_script_sees_an_already_diverged_submodule_change_commits(
    tmp_path: Path, sandbox: LocalShellBackend
) -> None:
    source = _checkout(tmp_path, "source", "https://github.com/org/submodule.git")
    (source / "src").mkdir()
    commits = []
    for content in ("one", "two", "three"):
        (source / "src" / "file").write_text(content)
        _git("-C", str(source), "add", "src/file")
        _git("-C", str(source), "commit", "-m", content)
        commits.append(_git("-C", str(source), "rev-parse", "HEAD"))

    repo = _checkout(tmp_path, "repo", "https://github.com/org/actual.git")
    _git(
        "-c",
        "protocol.file.allow=always",
        "-C",
        str(repo),
        "submodule",
        "add",
        str(source),
        "module",
    )
    module = repo / "module"
    _git("-C", str(module), "checkout", commits[0])
    _git("-C", str(repo), "add", ".")
    _git("-C", str(repo), "commit", "-m", "add submodule")
    _git("-C", str(module), "checkout", commits[1])
    before = GitSnapshot.model_validate(
        await SandboxScript("git_trace.py").run(sandbox, {"root": str(repo)})
    )
    stat = module.stat()

    _git("-C", str(module), "checkout", commits[2])
    after = GitSnapshot.model_validate(
        await SandboxScript("git_trace.py").run(sandbox, {"root": str(repo)})
    )

    assert module.stat().st_mtime_ns == stat.st_mtime_ns
    assert module.stat().st_ctime_ns == stat.st_ctime_ns
    assert [change.repo for change in after.changes_since(before)] == ["org/actual"]


async def test_a_detached_checkout_has_its_commit_and_no_branch(
    tmp_path: Path, sandbox: LocalShellBackend
) -> None:
    repo = _checkout(tmp_path, "repo", "https://github.com/org/actual")
    head = _git("-C", str(repo), "rev-parse", "HEAD")
    _git("-C", str(repo), "checkout", "--detach")

    snapshot = GitSnapshot.model_validate(
        await SandboxScript("git_trace.py").run(sandbox, {"root": str(tmp_path)})
    )

    assert snapshot.repos[str(repo)] == GitCheckout(
        repo="org/actual", branch="", head=head, dirty=snapshot.repos[str(repo)].dirty
    )


async def test_a_skipped_checkout_logs_its_reason_in_the_message(
    tmp_path: Path, sandbox: LocalShellBackend, caplog: pytest.LogCaptureFixture
) -> None:
    broken = _checkout(tmp_path, "broken", "https://github.com/org/actual")
    (broken / ".git" / "index").write_bytes(b"corrupt")
    git = TurnGit()

    with caplog.at_level("WARNING", logger="rt.git_evidence"):
        git.start(sandbox, str(tmp_path))
        await git.changing()

    assert f"Skipped checkouts in turn git evidence: {broken} (git failed)" in caplog.messages
