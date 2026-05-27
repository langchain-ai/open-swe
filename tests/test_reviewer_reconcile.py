from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.reviewer_reconcile import reconcile_findings_with_review_threads


@pytest.mark.asyncio
async def test_reconcile_marks_resolved_github_thread_resolved() -> None:
    findings = [
        {
            "id": "f1",
            "status": "open",
            "github_review_comment_id": 11,
            "github_review_thread_id": "THREAD_1",
        }
    ]
    replace = AsyncMock()

    with (
        patch("agent.reviewer_reconcile.list_findings", AsyncMock(return_value=findings)),
        patch("agent.reviewer_reconcile.replace_findings", replace),
    ):
        result = await reconcile_findings_with_review_threads(
            "tid",
            [
                {
                    "id": "THREAD_1",
                    "is_resolved": True,
                    "is_outdated": False,
                    "comments": [{"id": 11, "author": "open-swe[bot]", "body": "bug"}],
                }
            ],
        )

    assert result[0]["status"] == "resolved"
    assert result[0]["github_thread_resolved"] is True
    replace.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_backfills_comment_and_thread_ids_from_bot_marker() -> None:
    findings = [
        {
            "id": "f1",
            "status": "open",
            "github_review_comment_id": None,
            "github_review_thread_id": None,
        }
    ]
    replace = AsyncMock()

    with (
        patch("agent.reviewer_reconcile.list_findings", AsyncMock(return_value=findings)),
        patch("agent.reviewer_reconcile.replace_findings", replace),
    ):
        result = await reconcile_findings_with_review_threads(
            "tid",
            [
                {
                    "id": "THREAD_1",
                    "is_resolved": False,
                    "is_outdated": False,
                    "comments": [
                        {
                            "id": 11,
                            "author": "open-swe[bot]",
                            "body": (
                                '<!-- open-swe-review-comment {"id":"f1",'
                                '"file_path":"a.py","start_line":1,'
                                '"end_line":1,"side":"RIGHT"} -->\n\nbug'
                            ),
                        }
                    ],
                }
            ],
        )

    assert result[0]["github_review_comment_id"] == 11
    assert result[0]["github_review_comment_ids"] == [11]
    assert result[0]["github_review_thread_id"] == "THREAD_1"
    assert result[0]["github_review_thread_ids"] == ["THREAD_1"]
    replace.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_ignores_spoofed_non_bot_marker() -> None:
    findings = [
        {
            "id": "f1",
            "status": "open",
            "github_review_comment_id": None,
            "github_review_thread_id": None,
        }
    ]
    replace = AsyncMock()

    with (
        patch("agent.reviewer_reconcile.list_findings", AsyncMock(return_value=findings)),
        patch("agent.reviewer_reconcile.replace_findings", replace),
    ):
        result = await reconcile_findings_with_review_threads(
            "tid",
            [
                {
                    "id": "THREAD_1",
                    "is_resolved": False,
                    "is_outdated": False,
                    "comments": [
                        {
                            "id": 11,
                            "author": "human",
                            "body": (
                                '<!-- open-swe-review-comment {"id":"f1",'
                                '"file_path":"a.py","start_line":1,'
                                '"end_line":1,"side":"RIGHT"} -->\n\nspoof'
                            ),
                        }
                    ],
                }
            ],
        )

    assert result[0]["github_review_comment_id"] is None
    assert result[0]["github_review_thread_id"] is None
    replace.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_records_latest_human_reply_after_bot_comment() -> None:
    findings = [{"id": "f1", "status": "open", "github_review_comment_id": 11}]
    replace = AsyncMock()

    with (
        patch("agent.reviewer_reconcile.list_findings", AsyncMock(return_value=findings)),
        patch("agent.reviewer_reconcile.replace_findings", replace),
    ):
        result = await reconcile_findings_with_review_threads(
            "tid",
            [
                {
                    "id": "THREAD_1",
                    "is_resolved": False,
                    "is_outdated": False,
                    "comments": [
                        {"id": 11, "author": "open-swe[bot]", "body": "bug"},
                        {
                            "id": 12,
                            "author": "human",
                            "body": "This is not valid because the caller already guards it.",
                            "created_at": "2026-05-26T10:00:00Z",
                        },
                    ],
                }
            ],
        )

    assert result[0]["github_review_thread_id"] == "THREAD_1"
    assert result[0]["last_human_reply_author"] == "human"
    assert "not valid" in result[0]["last_human_reply_body"]
    replace.assert_awaited_once()
