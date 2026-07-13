"""Unit tests for the unified-diff parsing helpers."""

from __future__ import annotations

import pytest

from agent.review.diff import (
    changed_files,
    compute_diff_line_set,
    extract_diff_hunk,
    is_range_in_diff,
    materialize_review_diff,
    parse_unified_diff,
    review_diff_range,
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


def test_parse_unified_diff_extracts_hunks_per_file() -> None:
    files = parse_unified_diff(_TWO_FILE_DIFF)
    assert [fd.file for fd in files] == ["foo.py", "bar.py"]
    assert len(files[0].hunks) == 1
    assert len(files[1].hunks) == 2


def test_compute_diff_line_set_covers_each_hunks_new_lines() -> None:
    line_set = compute_diff_line_set(_TWO_FILE_DIFF)
    assert line_set["foo.py"]["RIGHT"] == {10, 11, 12, 13}
    assert line_set["bar.py"]["RIGHT"] == {1, 2, 3, 51, 52, 53, 54}


def test_compute_diff_line_set_also_covers_old_side_lines() -> None:
    """LEFT-side findings anchor to deleted/old-side lines; the line set
    must expose those so add_finding doesn't wrongly reject them."""
    line_set = compute_diff_line_set(_TWO_FILE_DIFF)
    assert line_set["foo.py"]["LEFT"] == {10, 11, 12}
    assert line_set["bar.py"]["LEFT"] == {1, 2, 50, 51, 52}


def test_is_range_in_diff_for_inline_and_file_level() -> None:
    line_set = compute_diff_line_set(_TWO_FILE_DIFF)
    assert is_range_in_diff(line_set, "foo.py", 11, 12) is True
    assert is_range_in_diff(line_set, "foo.py", 11, 99) is False
    assert is_range_in_diff(line_set, "missing.py", 1, 1) is False
    assert is_range_in_diff(line_set, "foo.py", None, None) is True


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


def test_extract_diff_hunk_returns_overlapping_hunk_body() -> None:
    hunk = extract_diff_hunk(_TWO_FILE_DIFF, "bar.py", 51, 52)
    assert hunk is not None
    assert "@@ -50,3 +51,4 @@" in hunk
    assert "line_b" in hunk


def test_extract_diff_hunk_returns_none_for_unknown_file() -> None:
    assert extract_diff_hunk(_TWO_FILE_DIFF, "unknown.py", 1, 1) is None


@pytest.mark.parametrize(
    ("start", "end"),
    [(1, 1), (1, 3)],
)
def test_extract_diff_hunk_supports_single_line_and_range(start: int, end: int) -> None:
    hunk = extract_diff_hunk(_TWO_FILE_DIFF, "bar.py", start, end)
    assert hunk is not None
    assert "import sys" in hunk


def test_review_diff_range_uses_previous_head_for_re_review() -> None:
    assert review_diff_range(
        base_sha="a" * 40,
        head_sha="c" * 40,
        last_reviewed_sha="b" * 40,
        re_review=True,
    ) == ("b" * 40, "c" * 40, False)


def test_changed_files_returns_bounded_path_list() -> None:
    assert changed_files(_TWO_FILE_DIFF) == ["foo.py", "bar.py"]


@pytest.mark.asyncio
async def test_materialize_review_diff_reuses_existing_file() -> None:
    from unittest.mock import AsyncMock, MagicMock

    backend = MagicMock()
    backend.adownload_files = AsyncMock(return_value=[{"content": _TWO_FILE_DIFF.encode()}])
    backend.aupload_files = AsyncMock()

    result = await materialize_review_diff(
        backend,
        work_dir="/workspace",
        base_ref="a" * 40,
        head_ref="b" * 40,
        merge_base=True,
    )

    assert result.cached is True
    assert result.diff_text == _TWO_FILE_DIFF
    backend.aupload_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_materialize_review_diff_writes_supplied_diff() -> None:
    from unittest.mock import AsyncMock, MagicMock

    backend = MagicMock()
    backend.adownload_files = AsyncMock()
    backend.aupload_files = AsyncMock(return_value=[{"error": None}])

    result = await materialize_review_diff(
        backend,
        work_dir="/workspace",
        base_ref="a" * 40,
        head_ref="b" * 40,
        merge_base=True,
        diff_text=_TWO_FILE_DIFF,
    )

    assert result.cached is False
    uploaded_path, uploaded_content = backend.aupload_files.await_args.args[0][0]
    assert uploaded_path == result.path
    assert uploaded_content == _TWO_FILE_DIFF.encode()
    backend.adownload_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_compute_diff_in_sandbox_uses_three_dot_for_merge_base() -> None:
    """First-review path passes merge_base=True so we use base...head, not base..head."""
    from unittest.mock import MagicMock

    from agent.review.diff import compute_diff_in_sandbox

    backend = MagicMock()
    backend.execute = MagicMock(return_value="")

    await compute_diff_in_sandbox(
        backend, work_dir="/w", base_ref="base", head_ref="head", merge_base=True
    )
    cmd = backend.execute.call_args.args[0]
    assert "base...head" in cmd
    assert "base..head" not in cmd.replace("base...head", "")
    assert "--no-prefix" not in cmd  # invalid flag must not appear


@pytest.mark.asyncio
async def test_compute_diff_in_sandbox_uses_two_dot_by_default() -> None:
    """Re-review delta path passes merge_base=False so we use base..head."""
    from unittest.mock import MagicMock

    from agent.review.diff import compute_diff_in_sandbox

    backend = MagicMock()
    backend.execute = MagicMock(return_value="")

    await compute_diff_in_sandbox(backend, work_dir="/w", base_ref="oldsha", head_ref="newsha")
    cmd = backend.execute.call_args.args[0]
    assert "oldsha..newsha" in cmd
    assert "oldsha...newsha" not in cmd
