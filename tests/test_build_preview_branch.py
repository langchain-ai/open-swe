from pathlib import Path
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from scripts import build_preview_branch as preview


@pytest.fixture
def builder(monkeypatch: pytest.MonkeyPatch) -> preview.Preview:
    root = Path(preview.__file__).resolve().parent.parent
    monkeypatch.setattr(preview, "PROMPT_PATH", root / preview.PROMPT_PATH)
    monkeypatch.setattr(preview, "FIX_PROMPT_PATH", root / preview.FIX_PROMPT_PATH)
    return preview.Preview(
        preview.Settings(
            repo="langchain-ai/open-swe",
            branch="preview",
            label="preview",
            manual_branch="preview-manual",
            max_prs=50,
            reset_hour=7,
            reset_zone=ZoneInfo("America/New_York"),
            url="https://preview.test",
            agent_timeout_seconds=60,
            force=False,
        )
    )


@pytest.mark.parametrize(
    ("pull_result", "run_result"),
    [
        (preview.Completed(1, "", "registry unavailable\nretry later"), None),
        (preview.Completed(0, "pulled", ""), preview.Completed(125, "", "container failed")),
        (
            preview.Completed(0, "pulled", ""),
            preview.Completed(1, "", "docker: Cannot connect to the Docker daemon"),
        ),
        (
            preview.Completed(0, "pulled", ""),
            preview.Completed(1, "", "Unable to find image 'node:24-bookworm-slim' locally"),
        ),
    ],
)
async def test_unavailable_build_does_not_repair_or_publish(
    builder: preview.Preview,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pull_result: preview.Completed,
    run_result: preview.Completed | None,
) -> None:
    async def command(*args: str, **kwargs: object) -> preview.Completed:
        if args[:2] == ("docker", "pull"):
            return pull_result
        if args[:2] == ("docker", "run"):
            assert run_result is not None
            return run_result
        return preview.Completed(0, "", "")

    run = AsyncMock(side_effect=command)
    git = AsyncMock(return_value=preview.Completed(0, "", ""))
    repair = AsyncMock()
    cache = Mock(spec=preview.RerereCache)
    cache.restored_tree = "cached-tree"
    cache.restore = AsyncMock()
    cache.save = AsyncMock()
    assemble = AsyncMock()
    publish = AsyncMock()
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(preview, "run", run)
    monkeypatch.setattr(preview, "git", git)
    monkeypatch.setattr(preview, "rev_parse", AsyncMock(return_value="assembled"))
    monkeypatch.setattr(preview, "remote_refs", AsyncMock(return_value=[]))
    monkeypatch.setattr(preview, "fetch_main", AsyncMock())
    monkeypatch.setattr(preview, "fix_with_agent", repair)
    monkeypatch.setattr(preview, "RerereCache", Mock(return_value=cache))
    monkeypatch.setattr(preview.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(preview.shutil, "which", Mock(return_value="oswe"))
    monkeypatch.setattr(builder, "fetch_published", AsyncMock(return_value=None))
    monkeypatch.setattr(builder, "assemble", assemble)
    monkeypatch.setattr(builder, "publish", publish)

    with pytest.raises(preview.TypecheckUnavailable):
        await builder.build()

    repair.assert_not_awaited()
    cache.discard.assert_not_called()
    cache.save.assert_not_awaited()
    assemble.assert_awaited_once()
    publish.assert_not_awaited()
    assert not any(call.args[0] == "push" for call in git.await_args_list)
    pulls = [call for call in run.await_args_list if call.args[:2] == ("docker", "pull")]
    runs = [call for call in run.await_args_list if call.args[:2] == ("docker", "run")]
    assert len(pulls) == (3 if run_result is None else 1)
    assert len(runs) == (0 if run_result is None else 1)
    text = summary.read_text().lower()
    assert "nothing published" in text or "nothing was published" in text
    failure = pull_result if run_result is None else run_result
    assert failure.first_line.lower() in text


@pytest.mark.parametrize("errors", [None, "src/page.ts: error TS2322"])
async def test_executed_typecheck_keeps_success_and_repair_paths(
    builder: preview.Preview,
    monkeypatch: pytest.MonkeyPatch,
    errors: str | None,
) -> None:
    async def command(*args: str, **kwargs: object) -> preview.Completed:
        if args[:2] == ("docker", "run") and errors:
            return preview.Completed(2, errors, "")
        return preview.Completed(0, "", "")

    monkeypatch.setattr(preview, "run", AsyncMock(side_effect=command))
    git = AsyncMock(return_value=preview.Completed(0, "", ""))
    repair = AsyncMock()
    assemble = AsyncMock()
    cache = Mock(spec=preview.RerereCache)
    cache.restored_tree = "cached-tree"
    monkeypatch.setattr(preview, "git", git)
    monkeypatch.setattr(preview, "rev_parse", AsyncMock(return_value="assembled"))
    monkeypatch.setattr(preview, "remote_refs", AsyncMock(return_value=[]))
    monkeypatch.setattr(preview, "fix_with_agent", repair)
    monkeypatch.setattr(builder, "assemble", assemble)

    result = await builder.verify("merge prompt", cache)

    pushes = [call.args for call in git.await_args_list if call.args[0] == "push"]
    if errors:
        assert result is not None and errors in result
        cache.discard.assert_called_once()
        assemble.assert_awaited_once_with("merge prompt")
        repair.assert_awaited_once()
        assert pushes == [("push", "--force", "origin", f"assembled:{preview.FAILED_REF}")]
    else:
        assert result is None
        cache.discard.assert_not_called()
        assemble.assert_not_awaited()
        repair.assert_not_awaited()
        assert not pushes
