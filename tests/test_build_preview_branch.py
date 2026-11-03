from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts import build_preview_branch as preview


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


async def test_a_main_workflow_change_still_publishes_with_the_published_workflows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The build's token cannot push a workflow change, so main editing CI must not block preview."""
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
    workflow = work / preview.WORKFLOWS / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("published\n")
    (work / "file.txt").write_text("base\n")
    await preview.git("add", ".")
    await preview.git("commit", "-m", "base")
    await preview.git("push", "origin", "main")
    await preview.Preview(preview.Settings.from_env()).build()

    await preview.git("switch", "main")
    workflow.write_text("edited on main\n")
    (work / "file.txt").write_text("main\n")
    await preview.git("commit", "-am", "main edits CI")
    await preview.git("push", "origin", "main")
    await preview.Preview(preview.Settings.from_env()).build()

    await preview.git("fetch", "origin", "preview")
    assert (await preview.git("show", "FETCH_HEAD:file.txt")).stdout == "main\n"
    assert (await preview.git("show", f"FETCH_HEAD:{preview.WORKFLOWS}/ci.yml")).stdout == (
        "published\n"
    )

    # Nothing changed since: the build takes the unchanged-tree shortcut, with no typecheck.
    checked = AsyncMock(return_value=None)
    monkeypatch.setattr(preview, "typecheck", checked)
    await preview.Preview(preview.Settings.from_env()).build()
    checked.assert_not_awaited()
