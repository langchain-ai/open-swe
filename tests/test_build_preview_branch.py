from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts import build_preview_branch as builder
from scripts.build_preview_branch import Completed, Preview, PreviewError, RerereCache, Settings


@pytest.fixture
def preview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Preview, RerereCache]:
    monkeypatch.setenv("GH_REPO", "langchain-ai/open-swe")
    monkeypatch.setenv("FORCE", "false")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setattr(builder.shutil, "which", lambda command: "/bin/oswe")
    monkeypatch.setattr(builder.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(builder, "fix_with_agent", AsyncMock())
    cache_dir = tmp_path / "rr-cache"
    cache_dir.mkdir()
    monkeypatch.setattr(builder, "RERERE_DIR", cache_dir)
    cache = RerereCache(restored_tree="cached")
    monkeypatch.setattr(cache, "restore", AsyncMock())
    monkeypatch.setattr(cache, "save", AsyncMock())
    monkeypatch.setattr(builder, "RerereCache", lambda: cache)
    preview = Preview(Settings.from_env())
    monkeypatch.setattr(preview, "assemble", AsyncMock())
    monkeypatch.setattr(preview, "fetch_published", AsyncMock(return_value="published"))
    monkeypatch.setattr(preview, "reuse_fixup", AsyncMock())
    monkeypatch.setattr(preview, "publish", AsyncMock())
    return preview, cache


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
    preview: tuple[Preview, RerereCache],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def respond(*args: str, **kwargs: object) -> Completed:
        if args[:2] == ("docker", stage):
            return failure
        return Completed(0, "assembled" if args[:2] == ("git", "rev-parse") else "", "")

    commands = AsyncMock(side_effect=respond)
    monkeypatch.setattr(builder, "run", commands)
    instance, cache = preview
    with pytest.raises(builder.TypecheckUnavailable) as unavailable:
        await instance.build()

    assert str(unavailable.value) == failure.stderr
    summary = (tmp_path / "summary.md").read_text()
    assert "Typecheck unavailable — nothing published" in summary
    assert failure.first_line in summary
    assert cache.restored_tree == "cached"
    assert builder.RERERE_DIR.is_dir()
    instance.assemble.assert_awaited_once()
    instance.publish.assert_not_awaited()
    builder.fix_with_agent.assert_not_awaited()
    cache.save.assert_not_awaited()
    assert not any(call.args[:2] == ("git", "push") for call in commands.call_args_list)
    pulls = [call for call in commands.call_args_list if call.args[:2] == ("docker", "pull")]
    assert len(pulls) == (3 if stage == "pull" else 1)
    if stage == "pull":
        assert not any(call.args[:2] == ("docker", "run") for call in commands.call_args_list)


@pytest.mark.parametrize("checks", [[0], [1, 1, 0], [1, 1, 1]])
async def test_executed_typecheck_keeps_repair_and_publication_behavior(
    checks: list[int],
    preview: tuple[Preview, RerereCache],
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
    monkeypatch.setattr(builder, "run", commands)
    instance, cache = preview
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
        assert not builder.RERERE_DIR.exists()
        assert instance.assemble.await_count == 2
        builder.fix_with_agent.assert_awaited_once()
        assert "error TS2322" in builder.fix_with_agent.call_args.args[1]
    else:
        assert cache.restored_tree == "cached"
        instance.assemble.assert_awaited_once()
        builder.fix_with_agent.assert_not_awaited()
    failed_pushes = [
        call
        for call in commands.call_args_list
        if call.args[:2] == ("git", "push") and call.args[-1].endswith(builder.FAILED_REF)
    ]
    assert len(failed_pushes) == (1 if checks[-1] else 0)
