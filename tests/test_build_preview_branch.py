from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts import build_preview_branch as preview
from scripts.build_preview_branch import Completed, Preview, PreviewError, RerereCache, Settings


@pytest.mark.parametrize("failure", ["conflict", "unrelated", "fetch"])
async def test_manual_branch_failure_stops_publication(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("GH_REPO", "langchain-ai/open-swe")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setattr(preview.shutil, "which", lambda _: None)
    monkeypatch.setattr(preview, "typecheck", AsyncMock(return_value=None))
    monkeypatch.setattr(preview, "open_pulls", AsyncMock(return_value=[]))
    await preview.git("init", "--bare", str(origin))
    await preview.git("init", "-b", "main")
    await preview.git("config", "user.name", "Preview test")
    await preview.git("config", "user.email", "preview@example.com")
    await preview.git("remote", "add", "origin", str(origin))
    (work / "file.txt").write_text("base\n")
    await preview.git("add", ".")
    await preview.git("commit", "-m", "base")
    await preview.git("switch", "-c", "preview-manual")
    if failure == "unrelated":
        await preview.git("checkout", "--orphan", "unrelated")
    (work / "file.txt").write_text("manual\n")
    await preview.git("add", ".")
    await preview.git("commit", "-m", "manual")
    await preview.git("push", "origin", "HEAD:refs/heads/preview-manual")
    await preview.git("switch", "main")
    (work / "file.txt").write_text("main\n")
    await preview.git("commit", "-am", "main")
    await preview.git("push", "origin", "main")

    if failure == "fetch":
        git = preview.git

        async def fail_manual_fetch(
            *args: str, check: bool = True, env: dict[str, str] | None = None
        ) -> preview.Completed:
            if args[:1] == ("fetch",) and args[-1].startswith("refs/heads/preview-manual:"):
                return preview.Completed(1, "", "manual branch fetch failed")
            return await git(*args, check=check, env=env)

        monkeypatch.setattr(preview, "git", fail_manual_fetch)

    with pytest.raises(preview.PreviewError, match="(?s)preview-manual.*nothing was published"):
        await preview.Preview(preview.Settings.from_env()).build()

    assert not await preview.remote_branch_exists("preview")
    assert not (tmp_path / "output").exists()
    summary = (tmp_path / "summary.md").read_text()
    assert "preview-manual" in summary
    assert "nothing published" in summary
    if failure == "conflict":
        assert "file.txt" in summary


@pytest.fixture
def stubbed_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Preview, RerereCache]:
    monkeypatch.setenv("GH_REPO", "langchain-ai/open-swe")
    monkeypatch.setenv("FORCE", "false")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setattr(preview.shutil, "which", lambda command: "/bin/oswe")
    monkeypatch.setattr(preview.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(preview, "fix_with_agent", AsyncMock())
    cache_dir = tmp_path / "rr-cache"
    cache_dir.mkdir()
    monkeypatch.setattr(preview, "RERERE_DIR", cache_dir)
    cache = RerereCache(restored_tree="cached")
    monkeypatch.setattr(cache, "restore", AsyncMock())
    monkeypatch.setattr(cache, "save", AsyncMock())
    monkeypatch.setattr(preview, "RerereCache", lambda: cache)
    instance = Preview(Settings.from_env())
    monkeypatch.setattr(instance, "assemble", AsyncMock())
    monkeypatch.setattr(instance, "fetch_published", AsyncMock(return_value="published"))
    monkeypatch.setattr(instance, "reuse_fixup", AsyncMock())
    monkeypatch.setattr(instance, "publish", AsyncMock())
    return instance, cache


@pytest.mark.parametrize(
    ("stage", "failure"),
    [
        ("pull", Completed(1, "", "unauthorized: authentication required\nlast pull detail")),
        ("run", Completed(125, "", "container creation failed\nmore detail")),
        ("run", Completed(1, "", "Cannot connect to the Docker daemon at unix:///docker.sock")),
        ("run", Completed(1, "", "docker: Error response from daemon: image not found")),
        ("run", Completed(1, "", "Unable to find image 'node:24-bookworm-slim' locally")),
    ],
)
async def test_unavailable_typecheck_leaves_preview_retryable(
    stage: str,
    failure: Completed,
    stubbed_build: tuple[Preview, RerereCache],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def respond(*args: str, **kwargs: object) -> Completed:
        if args[:2] == ("docker", stage):
            return failure
        return Completed(0, "assembled" if args[:2] == ("git", "rev-parse") else "", "")

    commands = AsyncMock(side_effect=respond)
    monkeypatch.setattr(preview, "run", commands)
    instance, cache = stubbed_build
    with pytest.raises(preview.TypecheckUnavailable) as unavailable:
        await instance.build()

    assert str(unavailable.value) == failure.stderr
    summary = (tmp_path / "summary.md").read_text()
    assert "Typecheck unavailable — nothing published" in summary
    assert failure.first_line in summary
    assert cache.restored_tree == "cached"
    assert preview.RERERE_DIR.is_dir()
    instance.assemble.assert_awaited_once()
    instance.publish.assert_not_awaited()
    preview.fix_with_agent.assert_not_awaited()
    cache.save.assert_not_awaited()
    assert not any(call.args[:2] == ("git", "push") for call in commands.call_args_list)
    pulls = [call for call in commands.call_args_list if call.args[:2] == ("docker", "pull")]
    assert len(pulls) == (3 if stage == "pull" else 1)
    if stage == "pull":
        assert not any(call.args[:2] == ("docker", "run") for call in commands.call_args_list)


@pytest.mark.parametrize("checks", [[0], [1, 1, 0], [1, 1, 1]])
async def test_executed_typecheck_keeps_repair_and_publication_behavior(
    checks: list[int],
    stubbed_build: tuple[Preview, RerereCache],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = deque(checks)
    pulls = deque([1, 0])

    async def respond(*args: str, **kwargs: object) -> Completed:
        if args[:2] == ("docker", "pull"):
            return Completed(pulls.popleft() if pulls else 0, "", "transient registry error")
        if args[:2] == ("docker", "run"):
            code = results.popleft()
            return Completed(code, "dashboard.ts: error TS2322" if code else "", "")
        return Completed(0, "assembled" if args[:2] == ("git", "rev-parse") else "", "")

    commands = AsyncMock(side_effect=respond)
    monkeypatch.setattr(preview, "run", commands)
    instance, cache = stubbed_build
    if checks[-1]:
        with pytest.raises(PreviewError, match="the preview tree fails typecheck"):
            await instance.build()
        instance.publish.assert_not_awaited()
    else:
        await instance.build()
        instance.publish.assert_awaited_once_with("published")

    assert not results
    if checks[0]:
        assert cache.restored_tree is None
        assert not preview.RERERE_DIR.exists()
        assert instance.assemble.await_count == 2
        preview.fix_with_agent.assert_awaited_once()
        assert "error TS2322" in preview.fix_with_agent.call_args.args[1]
    else:
        assert cache.restored_tree == "cached"
        instance.assemble.assert_awaited_once()
        preview.fix_with_agent.assert_not_awaited()
    failed_pushes = [
        call
        for call in commands.call_args_list
        if call.args[:2] == ("git", "push") and call.args[-1].endswith(preview.FAILED_REF)
    ]
    assert len(failed_pushes) == (1 if checks[-1] else 0)
