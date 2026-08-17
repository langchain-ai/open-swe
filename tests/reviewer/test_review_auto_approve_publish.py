from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent.review.findings import Finding, new_finding
from agent.tools.publish_review import _publish_review_async


def _policy() -> dict[str, Any]:
    return {
        "team_enabled": True,
        "team_threshold": 90,
        "repo_enabled": True,
        "repo_threshold": None,
        "effective_enabled": True,
        "effective_threshold": 90,
    }


def _preflight(**overrides: Any) -> dict[str, Any]:
    return {
        "live_head_sha": "head",
        "pr_open": True,
        "pr_draft": False,
        "pr_author": "alice",
        "app_login": "open-swe[bot]",
        "active_human_change_requests": [],
        "duplicate_approval_review_id": None,
        **overrides,
    }


def _finding() -> Finding:
    return new_finding(
        severity="medium",
        confidence="high",
        category="correctness",
        file="x.py",
        start_line=1,
        end_line=1,
        description="Issue",
        sha="head",
    )


async def _run(
    *,
    findings: list[Finding] | None = None,
    preflight: dict[str, Any] | None = None,
    is_re_review: bool = False,
) -> tuple[dict[str, Any], AsyncMock, AsyncMock]:
    post_review = AsyncMock(return_value={"id": 42})
    persist = AsyncMock()
    with ExitStack() as stack:
        stack.enter_context(
            patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid")
        )
        stack.enter_context(
            patch(
                "agent.tools.publish_review._backfill_findings_from_pr_threads",
                AsyncMock(return_value=findings or []),
            )
        )
        stack.enter_context(
            patch(
                "agent.tools.publish_review.get_effective_review_approval_policy",
                AsyncMock(return_value=_policy()),
            )
        )
        stack.enter_context(
            patch(
                "agent.tools.publish_review.fetch_approval_preflight",
                AsyncMock(return_value=preflight or _preflight()),
            )
        )
        stack.enter_context(
            patch("agent.tools.publish_review.persist_approval_assessment", persist)
        )
        stack.enter_context(
            patch("agent.tools.publish_review.post_pull_request_review", post_review)
        )
        stack.enter_context(
            patch(
                "agent.tools.publish_review._open_swe_already_reviewed",
                AsyncMock(return_value=False),
            )
        )
        stack.enter_context(
            patch(
                "agent.tools.publish_review._resolve_threads_for_resolved_findings",
                AsyncMock(return_value=0),
            )
        )
        stack.enter_context(
            patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock())
        )
        stack.enter_context(
            patch("agent.tools.publish_review.clear_review_started_comment", AsyncMock())
        )
        stack.enter_context(
            patch("agent.tools.publish_review.settle_review_check_run", AsyncMock())
        )
        stack.enter_context(
            patch("agent.tools.publish_review._maybe_post_slack_completion_reply", AsyncMock())
        )
        stack.enter_context(
            patch(
                "agent.tools.publish_review._resolve_review_trace_url", AsyncMock(return_value=None)
            )
        )
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="head",
            token="token",
            severity_threshold="medium",
            cap=6,
            is_re_review=is_re_review,
            approval_score=95,
            approval_reasons=["Reviewed the complete diff"],
            approval_risks=[],
        )
    return result, post_review, persist


@pytest.mark.asyncio
async def test_eligible_review_posts_approval() -> None:
    result, post_review, persist = await _run()

    assert result["review_event"] == "APPROVE"
    assert result["approval_decision"] == "approved"
    assert post_review.await_args is not None
    assert post_review.await_args.kwargs["event"] == "APPROVE"
    assert persist.await_args_list[-1].args[1]["github_review_id"] == 42


@pytest.mark.asyncio
async def test_stale_head_posts_comment() -> None:
    result, post_review, _ = await _run(preflight=_preflight(live_head_sha="new-head"))

    assert result["review_event"] == "COMMENT"
    assert "stale_head" in result["approval_blockers"]
    assert post_review.await_args is not None
    assert post_review.await_args.kwargs["event"] == "COMMENT"


@pytest.mark.asyncio
async def test_open_medium_finding_blocks_without_preflight() -> None:
    result, post_review, _ = await _run(findings=[_finding()])

    assert result["approval_score"] == 0
    assert "open_blocking_findings" in result["approval_blockers"]
    assert post_review.await_args is not None
    assert post_review.await_args.kwargs["event"] == "COMMENT"


@pytest.mark.asyncio
async def test_existing_matching_approval_is_idempotent() -> None:
    result, post_review, persist = await _run(preflight=_preflight(duplicate_approval_review_id=99))

    assert result["duplicate_approval"] is True
    assert result["review_id"] == 99
    post_review.assert_not_awaited()
    assert persist.await_args_list[-1].args[1]["decision"] == "skipped_duplicate"


@pytest.mark.asyncio
async def test_clean_re_review_can_post_approval() -> None:
    result, post_review, _ = await _run(is_re_review=True)

    assert result["review_event"] == "APPROVE"
    post_review.assert_awaited_once()
