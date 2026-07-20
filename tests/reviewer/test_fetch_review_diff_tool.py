from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.review.diff import MaterializedReviewDiff
from agent.tools.fetch_review_diff import fetch_review_diff


@pytest.mark.asyncio
async def test_fetch_review_diff_returns_metadata_without_diff_body() -> None:
    diff_text = "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
    materialized = MaterializedReviewDiff(
        path="/workspace/review-diff.patch",
        diff_text=diff_text,
        base_ref="a" * 40,
        head_ref="b" * 40,
        merge_base=True,
        cached=True,
    )
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "repo": {"owner": "acme", "name": "repo"},
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
        }
    }

    with (
        patch("agent.tools.fetch_review_diff.get_config", return_value=config),
        patch("agent.tools.fetch_review_diff.get_cached_sandbox_backend", return_value=MagicMock()),
        patch(
            "agent.tools.fetch_review_diff.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.tools.fetch_review_diff.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=materialized,
        ) as mock_materialize,
    ):
        result = await fetch_review_diff()

    assert result == {
        "success": True,
        "path": "/workspace/review-diff.patch",
        "bytes": len(diff_text.encode()),
        "files": ["app.py"],
        "file_count": 1,
        "files_truncated": False,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "cached": True,
    }
    assert diff_text not in str(result)
    assert mock_materialize.await_args is not None
    assert mock_materialize.await_args.kwargs["work_dir"] == "/workspace/repo"
    assert mock_materialize.await_args.kwargs["merge_base"] is True


@pytest.mark.asyncio
async def test_fetch_review_diff_uses_incremental_range_for_re_review() -> None:
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "repo": {"owner": "acme", "name": "repo"},
            "base_sha": "a" * 40,
            "last_reviewed_sha": "b" * 40,
            "head_sha": "c" * 40,
            "re_review": True,
        }
    }
    materialized = MaterializedReviewDiff(
        path="/workspace/review-diff.patch",
        diff_text="",
        base_ref="b" * 40,
        head_ref="c" * 40,
        merge_base=False,
        cached=False,
    )

    with (
        patch("agent.tools.fetch_review_diff.get_config", return_value=config),
        patch("agent.tools.fetch_review_diff.get_cached_sandbox_backend", return_value=MagicMock()),
        patch(
            "agent.tools.fetch_review_diff.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.tools.fetch_review_diff.materialize_review_diff",
            new_callable=AsyncMock,
            return_value=materialized,
        ) as mock_materialize,
    ):
        result = await fetch_review_diff()

    assert result["base_sha"] == "b" * 40
    assert mock_materialize.await_args is not None
    assert mock_materialize.await_args.kwargs == {
        "work_dir": "/workspace/repo",
        "base_ref": "b" * 40,
        "head_ref": "c" * 40,
        "merge_base": False,
    }
