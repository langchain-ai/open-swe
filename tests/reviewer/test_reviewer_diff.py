"""Unit tests for the unified-diff parsing helpers."""

from pathlib import Path

import pytest

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


@pytest.mark.asyncio
@pytest.mark.parametrize("merge_base,pr_change", [(False, True), (True, False), (True, True)])
async def test_re_review_excludes_new_base_changes(
    tmp_path: Path, merge_base: bool, pr_change: bool
) -> None:
    import asyncio
    import subprocess
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from openswe.review.diff import materialize_review_diff, review_diff_range
    from openswe.utils.agents_md import fetch_scoped_agents_md

    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(tmp_path), *args]).decode().strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AGENTS.md").write_text("trusted scoped rules")
    (tmp_path / "base.txt").write_text("base\n")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "feature")
    (tmp_path / "src" / "pr.txt").write_text("reviewed\n")
    git("add", ".")
    git("commit", "-qm", "reviewed")
    reviewed = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")
    (tmp_path / "base.txt").write_text("main only\n" * 100_000)
    git("add", ".")
    git("commit", "-qm", "main change")
    base = git("rev-parse", "HEAD")
    git("checkout", "-q", "feature")
    if merge_base:
        git("merge", "-qm", "merge main", "main")
    if pr_change:
        (tmp_path / "src" / "pr.txt").write_text("new PR change\n")
        git("add", ".")
        git("commit", "-qm", "PR change")
    head = git("rev-parse", "HEAD")

    async def execute(command: str) -> SimpleNamespace:
        process = await asyncio.create_subprocess_shell(command, stdout=asyncio.subprocess.PIPE)
        output, _ = await process.communicate()
        return SimpleNamespace(exit_code=process.returncode, output=output.decode())

    async def upload(files: list[tuple[str, bytes]]) -> list[SimpleNamespace]:
        for path, content in files:
            Path(path).write_bytes(content)
        return [SimpleNamespace(error=None) for _ in files]

    async def download(paths: list[str]) -> list[SimpleNamespace]:
        return [SimpleNamespace(content=None, error="not found") for _ in paths]

    backend = MagicMock(aexecute=execute, aupload_files=upload, adownload_files=download)
    start, end, three_dot = await review_diff_range(
        backend,
        work_dir=str(tmp_path),
        base_sha=base,
        head_sha=head,
        last_reviewed_sha=reviewed,
        re_review=True,
    )
    materialized = await materialize_review_diff(
        backend, work_dir=str(tmp_path), base_ref=start, head_ref=end, merge_base=three_dot
    )
    diff = Path(materialized.path).read_text()
    if merge_base and not pr_change:
        docs = await fetch_scoped_agents_md(
            backend,
            str(tmp_path),
            base,
            ["src/pr.txt", *(f"dir{i}/nested/file.py" for i in range(20_000))],
        )
        assert docs == {"src/AGENTS.md": "trusted scoped rules"}
    assert "base.txt" not in diff
    assert len(diff) < 1000
    assert ("new PR change" in diff) == pr_change
    if not merge_base:
        assert start == reviewed
