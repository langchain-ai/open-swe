"""Unit tests for the unified-diff parsing helpers."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse

from openswe.review.diff import (
    compute_diff_line_set,
    extract_diff_hunk,
    is_range_in_diff,
)

_TWO_FILE_DIFF = """diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -10,3 +10,4 @@ def existing():
     pass
+    new_line_13 = 1
+    new_line_14 = 2
     return 1
diff --git a/bar.py b/bar.py
index 3333333..4444444 100644
--- a/bar.py
+++ b/bar.py
@@ -1,2 +1,3 @@
 import os
+import sys
 print(os.getcwd())
@@ -50,3 +51,4 @@ def other():
     line_a = 1
+    line_b = 2
     line_c = 3
"""


def test_compute_diff_line_set_also_covers_old_side_lines() -> None:
    """LEFT-side findings anchor to deleted/old-side lines; the line set
    must expose those so add_finding doesn't wrongly reject them."""
    line_set = compute_diff_line_set(_TWO_FILE_DIFF)
    assert line_set["foo.py"]["LEFT"] == {10, 11, 12}
    assert line_set["bar.py"]["LEFT"] == {1, 2, 50, 51, 52}


def test_is_range_in_diff_left_side_accepts_old_line_numbers() -> None:
    """A finding with side=LEFT must validate against the OLD-side line set,
    not the new-side. The new-side hunk for foo.py is +10..+13; the old-side
    is -10..-12. Asserting against the wrong side would falsely reject a
    valid deleted-line finding."""
    line_set = compute_diff_line_set(_TWO_FILE_DIFF)
    assert is_range_in_diff(line_set, "foo.py", 12, 12, side="LEFT") is True
    # And the same line on RIGHT side is also in-diff (it's context).
    assert is_range_in_diff(line_set, "foo.py", 12, 12, side="RIGHT") is True
    # A LEFT anchor on a line that doesn't exist on the old side must be rejected.
    assert is_range_in_diff(line_set, "foo.py", 13, 13, side="LEFT") is False


@pytest.mark.parametrize(
    ("start", "end"),
    [(1, 1), (1, 3)],
)
def test_extract_diff_hunk_supports_single_line_and_range(start: int, end: int) -> None:
    hunk = extract_diff_hunk(_TWO_FILE_DIFF, "bar.py", start, end)
    assert hunk is not None
    assert "import sys" in hunk


@pytest.mark.asyncio
async def test_compute_diff_in_sandbox_uses_three_dot_for_merge_base() -> None:
    """First-review path passes merge_base=True so we use base...head, not base..head."""
    from unittest.mock import AsyncMock, MagicMock

    from openswe.review.diff import compute_diff_in_sandbox

    backend = MagicMock()
    backend.aexecute = AsyncMock(return_value="")

    await compute_diff_in_sandbox(
        backend, work_dir="/w", base_ref="base", head_ref="head", merge_base=True
    )
    cmd = backend.aexecute.call_args.args[0]
    assert "base...head" in cmd
    assert "base..head" not in cmd.replace("base...head", "")
    assert "--no-prefix" not in cmd  # invalid flag must not appear


class _LocalReviewBackend:
    """Execute real Git commands and materialize patches without a remote sandbox."""

    async def aexecute(self, command: str) -> ExecuteResponse:
        process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        output, _ = await process.communicate()
        return ExecuteResponse(output=output.decode(), exit_code=process.returncode)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(path=path, content=Path(path).read_bytes())
            if Path(path).exists()
            else FileDownloadResponse(path=path, error="file_not_found")
            for path in paths
        ]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        for path, content in files:
            Path(path).write_bytes(content)
        return [FileUploadResponse(path=path) for path, _ in files]


@pytest.fixture
async def review_repo(tmp_path: Path) -> tuple[Path, _LocalReviewBackend]:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo, _LocalReviewBackend()


@pytest.mark.parametrize(
    "update", ["base-merge", "commit", "mixed", "feature-merge", "resolution", "edited-merge"]
)
async def test_re_review_range_materialization_and_tool(
    review_repo: tuple[Path, _LocalReviewBackend], update: str
) -> None:
    from openswe.review.diff import materialize_review_diff, review_diff_range
    from openswe.tools.fetch_review_diff import fetch_review_diff

    repo, backend = review_repo

    async def git(*args: str, check: bool = True) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repo),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        if check:
            assert process.returncode == 0, output.decode()
        return output.decode().strip()

    await git("init", "-b", "main")
    await git("config", "user.email", "review@example.com")
    await git("config", "user.name", "Review Test")
    await git("config", "commit.gpgsign", "false")
    (repo / "app.py").write_text("original\n")
    await git("add", ".")
    await git("commit", "-m", "initial")
    await git("checkout", "-b", "pr")
    (repo / "app.py").write_text("reviewed PR\n")
    await git("commit", "-am", "reviewed")
    last = await git("rev-parse", "HEAD")
    await git("checkout", "main")
    base_files = 20_000 if update == "base-merge" else 1
    upstream = repo / "upstream"
    upstream.mkdir()
    for i in range(base_files):
        directory = upstream / f"scope-{i // 5}"
        directory.mkdir(exist_ok=True)
        (directory / f"{i}.txt").write_text("base only\n")
    (upstream / "AGENTS.md").write_text("Trusted upstream instructions\n")
    if update == "resolution":
        (repo / "app.py").write_text("base edit\n")
    await git("add", ".")
    await git("commit", "-m", "upstream changes")
    base = await git("rev-parse", "HEAD")
    await git("checkout", "pr")
    if update in ("commit", "mixed"):
        (repo / "new.py").write_text("new PR code\n")
        await git("add", ".")
        await git("commit", "-m", "new PR commit")
    if update == "feature-merge":
        await git("checkout", "-b", "feature")
        (repo / "feature.py").write_text("feature code\n")
        await git("add", ".")
        await git("commit", "-m", "feature work")
        await git("checkout", "pr")
        await git("merge", "--no-ff", "feature", "-m", "merge feature")
    elif update != "commit":
        await git("merge", "--no-ff", "main", "-m", "merge base", check=update != "resolution")
        if update == "resolution":
            (repo / "app.py").write_text("authored resolution\n")
            await git("add", "app.py")
            await git("commit", "-m", "resolve base merge")
        elif update == "edited-merge":
            (repo / "app.py").write_text("extra merge edit\n")
            await git("commit", "-am", "merge with authored edit", "--amend")
    head = await git("rev-parse", "HEAD")
    refs = await review_diff_range(
        backend,
        work_dir=str(repo),
        base_sha=base,
        head_sha=head,
        last_reviewed_sha=last,
        re_review=True,
    )
    assert refs == (head if update == "base-merge" else last, head, False)
    materialized = await materialize_review_diff(
        backend,
        work_dir=str(repo),
        base_ref=refs[0],
        head_ref=refs[1],
        merge_base=refs[2],
    )
    expected = await git("diff", "--no-color", f"{refs[0]}..{head}")
    assert materialized.diff_text.strip() == expected
    assert Path(materialized.path).read_text() == materialized.diff_text
    if update == "base-merge":
        from openswe.remote_runtime.graph import _json_state
        from openswe.utils.agents_md import fetch_scoped_agents_md

        assert materialized.diff_text == ""
        scoped = await fetch_scoped_agents_md(
            backend,
            str(repo),
            base,
            [f"upstream/scope-{i // 5}/{i}.txt" for i in range(base_files)],
        )
        assert scoped == {"upstream/AGENTS.md": "Trusted upstream instructions\n"}
        state = _json_state(
            {
                "diff_path": materialized.path,
                "diff_line_set": compute_diff_line_set(materialized.diff_text),
            }
        )
        assert state == {"diff_path": materialized.path, "diff_line_set": {}}
    elif update in ("commit", "mixed"):
        assert "+new PR code" in materialized.diff_text
    elif update == "feature-merge":
        assert "+feature code" in materialized.diff_text
    else:
        assert (
            "+authored resolution" if update == "resolution" else "+extra merge edit"
        ) in materialized.diff_text

    config = {
        "configurable": {
            "thread_id": "review-thread",
            "repo": {"owner": "acme", "name": "repo"},
            "base_sha": base,
            "head_sha": head,
            "last_reviewed_sha": last,
            "re_review": True,
        }
    }
    with (
        patch("openswe.run_config.get_config", return_value=config),
        patch("openswe.tools.fetch_review_diff.get_cached_sandbox_backend", return_value=backend),
        patch(
            "openswe.tools.fetch_review_diff.resolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value=str(repo.parent),
        ),
    ):
        result = await fetch_review_diff()
    assert result["success"] is True
    assert result["cached"] is True
    assert result["path"] == materialized.path
    assert result["bytes"] == len(materialized.diff_text.encode())
    if update == "base-merge":
        assert result["files"] == []
        assert result["file_count"] == 0
        assert result["bytes"] == 0
