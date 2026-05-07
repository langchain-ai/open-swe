"""Unit tests for the publish_review rendering and orchestration helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.reviewer_findings import Finding, new_finding
from agent.reviewer_publish import (
    render_inline_comment_body,
    render_inline_comment_payload,
    render_review_body,
    resolve_review_thread,
)


def _f(**overrides: Any) -> Finding:
    base = new_finding(
        severity="high",
        category="correctness",
        file="src/foo.py",
        start_line=10,
        end_line=10,
        description="boom",
        sha="abc",
    )
    base.update(overrides)  # type: ignore[arg-type]
    return base


def test_render_inline_comment_body_without_suggestion() -> None:
    body = render_inline_comment_body(_f(description="just text"))
    assert body == "just text"


def test_render_inline_comment_body_with_suggestion_appends_block() -> None:
    body = render_inline_comment_body(
        _f(description="needs fix", suggestion="x = 1\nx += 1"),
    )
    assert "needs fix" in body
    assert "```suggestion" in body
    assert "x = 1\nx += 1" in body


def test_render_inline_comment_payload_single_line() -> None:
    payload = render_inline_comment_payload(_f(start_line=10, end_line=10))
    assert payload == {
        "path": "src/foo.py",
        "line": 10,
        "side": "RIGHT",
        "body": "boom",
    }


def test_render_inline_comment_payload_multi_line_uses_start_fields() -> None:
    payload = render_inline_comment_payload(_f(start_line=8, end_line=12))
    assert payload is not None
    assert payload["start_line"] == 8
    assert payload["start_side"] == "RIGHT"
    assert payload["line"] == 12


def test_render_inline_comment_payload_returns_none_for_file_level() -> None:
    payload = render_inline_comment_payload(_f(start_line=None, end_line=None))
    assert payload is None


def test_render_review_body_includes_summary_and_marker() -> None:
    body = render_review_body(
        pr_number=123,
        surfaced_count=2,
        total_open_count=3,
        severity_threshold="medium",
        summary="LGTM with two notes",
    )
    assert "LGTM with two notes" in body
    assert "<!-- open-swe-reviewer pr=123 -->" in body
    assert "Found 2 findings" in body
    assert "1 lower-severity finding" in body


def test_render_review_body_surfaces_no_findings_message() -> None:
    body = render_review_body(
        pr_number=99,
        surfaced_count=0,
        total_open_count=0,
        severity_threshold="medium",
        summary=None,
    )
    assert "No issues found" in body
    assert "<!-- open-swe-reviewer pr=99 -->" in body


def test_render_review_body_no_findings_keeps_agent_summary() -> None:
    body = render_review_body(
        pr_number=42,
        surfaced_count=0,
        total_open_count=0,
        severity_threshold="medium",
        summary="Reviewed PR — clean refactor, well-tested.",
    )
    assert "No issues found" in body
    assert "Reviewed PR — clean refactor, well-tested." in body


def test_render_review_body_no_surfaced_with_hidden_lower_severity() -> None:
    body = render_review_body(
        pr_number=7,
        surfaced_count=0,
        total_open_count=2,
        severity_threshold="medium",
        summary=None,
    )
    assert "No issues at or above `medium` severity" in body
    assert "2 lower-severity findings hidden" in body


@pytest.mark.asyncio
async def test_resolve_review_thread_returns_true_on_success() -> None:
    response = MagicMock()
    response.json.return_value = {
        "data": {"resolveReviewThread": {"thread": {"id": "T_1", "isResolved": True}}}
    }
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=response)

    with patch("agent.reviewer_publish.httpx.AsyncClient", return_value=client_cm):
        ok = await resolve_review_thread(thread_node_id="T_1", token="t")
    assert ok is True


@pytest.mark.asyncio
async def test_resolve_review_thread_returns_false_on_graphql_errors() -> None:
    response = MagicMock()
    response.json.return_value = {"errors": [{"message": "no perms"}]}
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=response)

    with patch("agent.reviewer_publish.httpx.AsyncClient", return_value=client_cm):
        ok = await resolve_review_thread(thread_node_id="T_1", token="t")
    assert ok is False


@pytest.mark.asyncio
async def test_publish_review_skips_findings_already_published() -> None:
    """Re-runs must not re-post findings that already have a github_review_comment_id."""
    from agent.tools.publish_review import _publish_review_async

    findings = [
        _f(id="f_old", severity="high", file="a.py", github_review_comment_id=42),
        _f(id="f_new", severity="high", file="b.py"),
    ]

    list_async = AsyncMock(return_value=findings)
    post_review = AsyncMock(return_value={"id": 999})
    fetch_comments = AsyncMock(return_value=[])
    set_metadata = AsyncMock()

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", list_async),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.fetch_review_comments", fetch_comments),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", set_metadata),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="sha",
            token="t",
            summary=None,
            severity_threshold="medium",
            cap=15,
        )

    assert result["success"] is True
    assert result["surfaced_count"] == 1
    posted = post_review.await_args.kwargs["inline_comments"]
    paths = {c["path"] for c in posted}
    assert paths == {"b.py"}


@pytest.mark.asyncio
async def test_publish_review_posts_summary_when_no_findings() -> None:
    """An empty findings list must still post a review so the user sees feedback."""
    from agent.tools.publish_review import _publish_review_async

    list_async = AsyncMock(return_value=[])
    post_review = AsyncMock(return_value={"id": 555})
    fetch_comments = AsyncMock(return_value=[])
    set_metadata = AsyncMock()

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", list_async),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.fetch_review_comments", fetch_comments),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", set_metadata),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="sha",
            token="t",
            summary=None,
            severity_threshold="medium",
            cap=15,
        )

    assert result["success"] is True
    assert result["surfaced_count"] == 0
    assert result["review_id"] == 555
    post_review.assert_awaited_once()
    posted_body = post_review.await_args.kwargs["body"]
    posted_inline = post_review.await_args.kwargs["inline_comments"]
    assert posted_inline == []
    assert "No issues found" in posted_body
