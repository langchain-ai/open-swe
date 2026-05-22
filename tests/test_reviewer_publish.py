"""Unit tests for the publish_review rendering and orchestration helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.reviewer_findings import Finding, new_finding
from agent.reviewer_publish import (
    post_pull_request_review,
    render_inline_comment_body,
    render_inline_comment_payload,
    render_review_body,
    resolve_review_thread,
)


def _f(**overrides: Any) -> Finding:
    base = new_finding(
        severity="high",
        confidence="high",
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


def test_render_review_body_with_findings_uses_potential_issue_phrasing() -> None:
    body = render_review_body(pr_number=123, surfaced_count=2)
    assert body.startswith("**Open SWE Review** found 2 potential issues.")
    assert "<!-- open-swe-reviewer pr=123 -->" in body


def test_render_review_body_singular_finding() -> None:
    body = render_review_body(pr_number=123, surfaced_count=1)
    assert body.startswith("**Open SWE Review** found 1 potential issue.")


def test_render_review_body_no_findings_message() -> None:
    body = render_review_body(pr_number=99, surfaced_count=0)
    assert "## ✅ Open SWE Review: No issues found" in body
    assert "Open SWE reviewed this PR and found no potential bugs to report." in body
    assert "<!-- open-swe-reviewer pr=99 -->" in body


def test_publish_review_eval_mode_does_not_call_github() -> None:
    from agent.tools.publish_review import publish_review

    findings = [
        _f(id="f_high", severity="high", file="a.py", start_line=1, end_line=1),
        _f(id="f_low", severity="low", file="b.py", start_line=2, end_line=2),
    ]

    with (
        patch(
            "agent.tools.publish_review.get_config",
            return_value={
                "configurable": {
                    "thread_id": "tid",
                    "repo": {"owner": "o", "name": "r"},
                    "pr_number": 7,
                    "head_sha": "sha",
                    "reviewer_eval": True,
                },
                "metadata": {},
            },
        ),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()) as set_meta,
        patch("agent.tools.publish_review.get_github_token") as get_token,
        patch("agent.tools.publish_review.post_pull_request_review", AsyncMock()) as post_review,
    ):
        result = publish_review()

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["surfaced_count"] == 1
    assert result["hidden_count"] == 1
    get_token.assert_not_called()
    post_review.assert_not_called()
    set_meta.assert_awaited_once_with("tid", last_reviewed_sha="sha")


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
async def test_post_pull_request_review_non_dict_body_surfaces_status_and_excerpt() -> None:
    """A non-dict GitHub response body must surface status code + body excerpt
    via ``_error`` rather than collapsing to a bare ``None`` (which the
    user-facing tool would render as the unhelpful ``Failed to POST PR review``)."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = ["unexpected", "list", "body"]
    response.text = '["unexpected", "list", "body"]'
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=response)

    with patch("agent.reviewer_publish.httpx.AsyncClient", return_value=client_cm):
        result = await post_pull_request_review(
            owner="o",
            repo="r",
            pr_number=1,
            head_sha="sha",
            body="b",
            inline_comments=[],
            token="t",
        )

    assert isinstance(result, dict)
    assert "_error" in result
    err = result["_error"]
    assert "HTTP 200" in err
    assert "non-dict" in err
    assert "unexpected" in err
    # The bare legacy string must not be the only signal anymore.
    assert err != "Failed to POST PR review"


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
        patch(
            "agent.tools.publish_review._maybe_post_slack_completion_reply",
            new_callable=AsyncMock,
        ),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="sha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
        )

    assert result["success"] is True
    assert result["surfaced_count"] == 1
    posted = post_review.await_args.kwargs["inline_comments"]
    paths = {c["path"] for c in posted}
    assert paths == {"b.py"}


@pytest.mark.asyncio
async def test_publish_review_skips_post_on_re_review_with_no_new_findings() -> None:
    """Re-review with nothing new to surface must not spam another comment."""
    from agent.tools.publish_review import _publish_review_async

    # All findings already have github_review_comment_id from the prior publish
    # (so none are "unpublished"), plus one previously-resolved finding whose
    # thread still needs to be resolved on GitHub.
    findings = [
        {
            "id": "f_old",
            "severity": "high",
            "category": "correctness",
            "file": "a.py",
            "start_line": 1,
            "end_line": 1,
            "side": "RIGHT",
            "description": "x",
            "suggestion": None,
            "status": "resolved",
            "first_seen_sha": "s",
            "last_confirmed_sha": "s",
            "github_review_comment_id": 100,
        },
    ]
    list_async = AsyncMock(return_value=findings)
    post_review = AsyncMock()
    set_metadata = AsyncMock()
    resolve_threads = AsyncMock(return_value=1)

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", list_async),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            resolve_threads,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", set_metadata),
        patch(
            "agent.tools.publish_review._maybe_post_slack_completion_reply",
            new_callable=AsyncMock,
        ),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="newsha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=True,
        )

    post_review.assert_not_called()
    resolve_threads.assert_awaited_once()
    set_metadata.assert_awaited_once()
    assert result["success"] is True
    assert result["review_id"] is None
    assert result["surfaced_count"] == 0
    assert result["resolved_thread_count"] == 1
    assert result["skipped_empty_re_review"] is True


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
        patch(
            "agent.tools.publish_review._maybe_post_slack_completion_reply",
            new_callable=AsyncMock,
        ),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="sha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
        )

    assert result["success"] is True
    assert result["surfaced_count"] == 0
    assert result["review_id"] == 555
    post_review.assert_awaited_once()
    posted_body = post_review.await_args.kwargs["body"]
    posted_inline = post_review.await_args.kwargs["inline_comments"]
    assert posted_inline == []
    assert "No issues found" in posted_body


@pytest.mark.asyncio
async def test_publish_review_posts_slack_reply_on_first_review_with_slack_ref() -> None:
    """A first review with a slack_thread metadata ref posts a one-line summary."""
    from agent.tools.publish_review import _publish_review_async

    metadata = {
        "kind": "reviewer",
        "slack_thread": {"channel_id": "C1", "thread_ts": "1234.5"},
    }
    slack_post = AsyncMock(return_value=True)

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review.post_pull_request_review",
            AsyncMock(return_value={"id": 42}),
        ),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch(
            "agent.tools.publish_review.get_thread_metadata",
            new_callable=AsyncMock,
            return_value=metadata,
        ),
        patch("agent.tools.publish_review.post_slack_thread_reply", slack_post),
    ):
        await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="sha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
        )

    slack_post.assert_awaited_once()
    args = slack_post.await_args.args
    assert args[0] == "C1"
    assert args[1] == "1234.5"
    assert "No issues found" in args[2]
    assert "https://github.com/o/r/pull/7#pullrequestreview-42" in args[2]


@pytest.mark.asyncio
async def test_publish_review_uses_plural_findings_in_slack_reply() -> None:
    """Surfaced count > 1 should pluralize 'issues' in the slack summary."""
    from agent.tools.publish_review import _publish_review_async

    findings = [
        _f(id="f1", file="a.py", start_line=1, end_line=1),
        _f(id="f2", file="b.py", start_line=2, end_line=2),
    ]
    metadata = {
        "kind": "reviewer",
        "slack_thread": {"channel_id": "C1", "thread_ts": "1234.5"},
    }
    slack_post = AsyncMock(return_value=True)

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch(
            "agent.tools.publish_review.post_pull_request_review",
            AsyncMock(return_value={"id": 99}),
        ),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch(
            "agent.tools.publish_review.get_thread_metadata",
            new_callable=AsyncMock,
            return_value=metadata,
        ),
        patch("agent.tools.publish_review.post_slack_thread_reply", slack_post),
    ):
        await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="sha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
        )

    slack_post.assert_awaited_once()
    text = slack_post.await_args.args[2]
    assert "found 2 potential issues" in text


@pytest.mark.asyncio
async def test_publish_review_skips_slack_reply_on_re_review() -> None:
    """Re-reviews must NOT post to Slack even when slack_thread metadata is set."""
    from agent.tools.publish_review import _publish_review_async

    metadata = {
        "kind": "reviewer",
        "slack_thread": {"channel_id": "C1", "thread_ts": "1234.5"},
    }
    slack_post = AsyncMock(return_value=True)
    get_metadata = AsyncMock(return_value=metadata)

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review.post_pull_request_review",
            AsyncMock(return_value={"id": 1}),
        ),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch("agent.tools.publish_review.get_thread_metadata", get_metadata),
        patch("agent.tools.publish_review.post_slack_thread_reply", slack_post),
    ):
        await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="sha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=True,
        )

    slack_post.assert_not_awaited()
    # Re-review path should also avoid even fetching the slack metadata.
    get_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_review_skips_slack_reply_when_no_slack_ref() -> None:
    """A review started from GitHub (no slack_thread metadata) must not post to Slack."""
    from agent.tools.publish_review import _publish_review_async

    slack_post = AsyncMock(return_value=True)

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review.post_pull_request_review",
            AsyncMock(return_value={"id": 1}),
        ),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch(
            "agent.tools.publish_review.get_thread_metadata",
            new_callable=AsyncMock,
            return_value={"kind": "reviewer"},
        ),
        patch("agent.tools.publish_review.post_slack_thread_reply", slack_post),
    ):
        await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="sha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
        )

    slack_post.assert_not_awaited()
