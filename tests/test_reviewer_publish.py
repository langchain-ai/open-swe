"""Unit tests for the publish_review rendering and orchestration helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.reviewer_findings import Finding, new_finding
from agent.reviewer_publish import (
    fetch_pr_review_threads,
    post_pull_request_review,
    render_inline_comment_body,
    render_inline_comment_payload,
    render_review_body,
    reply_to_review_comment,
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
    assert "<!-- open-swe-review-comment" in body
    assert '"id":"f_' in body
    assert "just text" in body
    assert "React with +1 or -1" not in body


def test_render_inline_comment_body_with_suggestion_appends_block() -> None:
    body = render_inline_comment_body(
        _f(description="needs fix", suggestion="x = 1\nx += 1"),
    )
    assert "needs fix" in body
    assert "```suggestion" in body
    assert "x = 1\nx += 1" in body


def test_render_inline_comment_payload_single_line() -> None:
    payload = render_inline_comment_payload(_f(start_line=10, end_line=10))
    assert payload is not None
    assert payload["path"] == "src/foo.py"
    assert payload["line"] == 10
    assert payload["side"] == "RIGHT"
    assert "boom" in payload["body"]
    assert "<!-- open-swe-review-comment" in payload["body"]


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


@pytest.mark.asyncio
async def test_fetch_pr_review_threads_parses_threads_and_comments() -> None:
    """GraphQL response is mapped into the simplified thread dicts."""
    response = MagicMock()
    response.json.return_value = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "THREAD_1",
                                "isResolved": True,
                                "isOutdated": False,
                                "path": "a/b.py",
                                "line": 37,
                                "originalLine": 37,
                                "comments": {
                                    "nodes": [
                                        {
                                            "databaseId": 101,
                                            "author": {"login": "open-swe[bot]"},
                                            "authorAssociation": "MEMBER",
                                            "body": "additionalTtlPrefixes removes lifecycle rules",
                                            "createdAt": "2026-05-23T10:00:00Z",
                                        },
                                        {
                                            "databaseId": 102,
                                            "author": {"login": "human"},
                                            "authorAssociation": "MEMBER",
                                            "body": "We added defaults in the template",
                                            "createdAt": "2026-05-24T11:00:00Z",
                                        },
                                    ]
                                },
                            },
                            {
                                "id": "THREAD_2",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "c.py",
                                "line": 9,
                                "originalLine": None,
                                "comments": {
                                    "nodes": [
                                        {
                                            "databaseId": 201,
                                            "author": {"login": "rev"},
                                            "authorAssociation": "CONTRIBUTOR",
                                            "body": "this looks fishy",
                                            "createdAt": "2026-05-24T12:00:00Z",
                                        }
                                    ]
                                },
                            },
                        ],
                    }
                }
            }
        }
    }
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=response)

    with patch("agent.reviewer_publish.httpx.AsyncClient", return_value=client_cm):
        threads = await fetch_pr_review_threads(owner="o", repo="r", pr_number=1, token="t")

    assert len(threads) == 2
    assert threads[0]["id"] == "THREAD_1"
    assert threads[0]["path"] == "a/b.py"
    assert threads[0]["is_resolved"] is True
    assert threads[0]["line"] == 37
    assert len(threads[0]["comments"]) == 2
    assert threads[0]["comments"][0]["id"] == 101
    assert threads[0]["comments"][1]["author"] == "human"
    assert "added defaults" in threads[0]["comments"][1]["body"]
    assert threads[1]["is_resolved"] is False


@pytest.mark.asyncio
async def test_fetch_pr_review_threads_returns_empty_on_http_error() -> None:
    import httpx

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(side_effect=httpx.HTTPError("boom"))

    with patch("agent.reviewer_publish.httpx.AsyncClient", return_value=client_cm):
        threads = await fetch_pr_review_threads(owner="o", repo="r", pr_number=1, token="t")
    assert threads == []


@pytest.mark.asyncio
async def test_reply_to_review_comment_posts_reply_payload() -> None:
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"id": 456, "body": "Thanks for the context."}
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=response)

    with patch("agent.reviewer_publish.httpx.AsyncClient", return_value=client_cm):
        result = await reply_to_review_comment(
            owner="o",
            repo="r",
            pr_number=7,
            review_comment_id=123,
            body="Thanks for the context.",
            token="t",
        )

    assert result == {"id": 456, "body": "Thanks for the context."}
    args = client_cm.post.await_args
    assert args.args[0] == "https://api.github.com/repos/o/r/pulls/7/comments/123/replies"
    assert args.kwargs["json"] == {"body": "Thanks for the context."}


@pytest.mark.asyncio
async def test_post_pull_request_review_tags_unresolved_anchor_on_422() -> None:
    """A GitHub 422 with 'Path could not be resolved' must be tagged as
    ``unresolved_anchor`` and carry the raw errors so the tool layer can act
    on it (drop offending findings + retry) instead of bubbling an opaque
    error string that the agent will only retry with identical args."""
    import httpx

    response = MagicMock()
    response.status_code = 422
    response.text = '{"errors":["Path could not be resolved"]}'
    response.json.return_value = {"errors": ["Path could not be resolved"]}
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Unprocessable Entity",
        request=MagicMock(),
        response=response,
    )

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
            inline_comments=[{"path": "missing.py", "line": 1, "side": "RIGHT", "body": "x"}],
            token="t",
        )

    assert isinstance(result, dict)
    assert result.get("_error_kind") == "unresolved_anchor"
    assert result.get("_status") == 422
    assert result.get("_raw_errors") == ["Path could not be resolved"]
    assert "HTTP 422" in result.get("_error", "")


@pytest.mark.asyncio
async def test_post_pull_request_review_tags_unresolved_anchor_on_line_error() -> None:
    """A 'Line could not be resolved' 422 must also be tagged as
    ``unresolved_anchor`` so a line that's not in the diff is treated the same
    way as a path that's not in the diff."""
    import httpx

    response = MagicMock()
    response.status_code = 422
    response.text = '{"errors":["Line could not be resolved"]}'
    response.json.return_value = {"errors": ["Line could not be resolved"]}
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Unprocessable Entity",
        request=MagicMock(),
        response=response,
    )

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
    assert result.get("_error_kind") == "unresolved_anchor"


@pytest.mark.asyncio
async def test_post_pull_request_review_does_not_tag_unrelated_422() -> None:
    """A 422 whose errors don't match the anchor patterns must NOT be tagged
    as ``unresolved_anchor`` — the retry path is only safe for known
    per-comment anchor failures."""
    import httpx

    response = MagicMock()
    response.status_code = 422
    response.text = '{"errors":["something else"]}'
    response.json.return_value = {"errors": ["something else"]}
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Unprocessable Entity",
        request=MagicMock(),
        response=response,
    )

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
    assert result.get("_error_kind") is None
    assert result.get("_raw_errors") == ["something else"]


@pytest.mark.asyncio
async def test_publish_review_drops_unresolvable_findings_and_retries_once() -> None:
    """When GitHub rejects the batch with an ``unresolved_anchor`` 422, the
    tool must filter the bad findings against the PR diff_line_set, re-POST
    with only the valid ones, return ``success=True``, and report the dropped
    finding ids via ``unresolvable_findings`` plus a corrective hint."""
    from agent.tools.publish_review import _publish_review_async

    findings = [
        _f(id="f_good", severity="high", file="in_diff.py", start_line=10, end_line=10),
        _f(id="f_bad", severity="high", file="not_in_diff.py", start_line=99, end_line=99),
    ]
    # The PR diff only covers in_diff.py:10. f_bad anchors to a file/line not
    # in the diff, so it must be dropped on retry.
    diff_line_set = {"in_diff.py": {"RIGHT": {10}, "LEFT": set()}}

    first_response = {
        "_error": "HTTP 422: ...",
        "_error_kind": "unresolved_anchor",
        "_raw_errors": ["Path could not be resolved"],
        "_status": 422,
    }
    retry_response = {"id": 7777}
    post_review = AsyncMock(side_effect=[first_response, retry_response])
    fetch_comments = AsyncMock(return_value=[])
    set_metadata = AsyncMock()

    with (
        patch(
            "agent.tools.publish_review.get_config",
            return_value={
                "configurable": {
                    "thread_id": "tid",
                    "diff_line_set": diff_line_set,
                },
            },
        ),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch(
            "agent.tools.publish_review.list_findings_async",
            AsyncMock(return_value=findings),
        ),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.fetch_review_comments", fetch_comments),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "agent.tools.publish_review._store_thread_ids_on_findings",
            new_callable=AsyncMock,
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

    assert post_review.await_count == 2
    # Retry must contain only the in-diff finding.
    retry_inline = post_review.await_args_list[1].kwargs["inline_comments"]
    assert {c["path"] for c in retry_inline} == {"in_diff.py"}
    assert result["success"] is True
    assert result["review_id"] == 7777
    assert result["surfaced_count"] == 1
    assert result["unresolvable_findings"] == ["f_bad"]
    assert "update_finding" in result["hint"]


@pytest.mark.asyncio
async def test_publish_review_reports_unresolvable_when_retry_still_fails() -> None:
    """If even the filtered retry fails, the tool surfaces
    ``success=False`` plus the offending finding ids and a hint — it must
    NOT collapse into the opaque retry-with-same-args loop."""
    from agent.tools.publish_review import _publish_review_async

    findings = [
        _f(id="f_good", severity="high", file="in_diff.py", start_line=10, end_line=10),
        _f(id="f_bad", severity="high", file="not_in_diff.py", start_line=99, end_line=99),
    ]
    diff_line_set = {"in_diff.py": {"RIGHT": {10}, "LEFT": set()}}

    first_response = {
        "_error": "HTTP 422: ...",
        "_error_kind": "unresolved_anchor",
        "_raw_errors": ["Path could not be resolved"],
        "_status": 422,
    }
    retry_response = {"_error": "HTTP 500: boom"}
    post_review = AsyncMock(side_effect=[first_response, retry_response])

    with (
        patch(
            "agent.tools.publish_review.get_config",
            return_value={
                "configurable": {
                    "thread_id": "tid",
                    "diff_line_set": diff_line_set,
                },
            },
        ),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch(
            "agent.tools.publish_review.list_findings_async",
            AsyncMock(return_value=findings),
        ),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
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

    assert result["success"] is False
    assert result["unresolvable_findings"] == ["f_bad"]
    assert "update_finding" in result["hint"]


@pytest.mark.asyncio
async def test_publish_review_does_not_retry_when_no_findings_can_be_dropped() -> None:
    """When the unresolved_anchor 422 fires but the diff_line_set rules out
    no findings (e.g., diff data unavailable), the tool must NOT retry — it
    must surface the structured error so the agent stops looping."""
    from agent.tools.publish_review import _publish_review_async

    findings = [
        _f(id="f_only", severity="high", file="in_diff.py", start_line=10, end_line=10),
    ]
    # No cached diff_line_set, and the on-demand fetch fails — no way to tell
    # which finding is bad.
    first_response = {
        "_error": "HTTP 422: ...",
        "_error_kind": "unresolved_anchor",
        "_raw_errors": ["Path could not be resolved"],
        "_status": 422,
    }
    post_review = AsyncMock(return_value=first_response)

    with (
        patch(
            "agent.tools.publish_review.get_config",
            return_value={"configurable": {"thread_id": "tid"}},
        ),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch(
            "agent.tools.publish_review.list_findings_async",
            AsyncMock(return_value=findings),
        ),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch(
            "agent.tools.publish_review._resolve_diff_line_set",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
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

    # Only one attempt — never retry blindly.
    assert post_review.await_count == 1
    assert result["success"] is False
    assert result["unresolvable_findings"] == []
    assert "update_finding" in result["hint"]


@pytest.mark.asyncio
async def test_publish_review_fetches_pr_diff_when_diff_line_set_missing() -> None:
    """Reviewer runs clear ``diff_line_set`` from config before the agent
    starts, so the publish-time retry path must fall back to fetching the
    PR's unified diff on demand and recomputing the line set — otherwise no
    finding is ever droppable and the retry surfaces empty
    ``unresolvable_findings`` for the reachable production case."""
    from agent.tools.publish_review import _publish_review_async

    findings = [
        _f(id="f_good", severity="high", file="in_diff.py", start_line=10, end_line=10),
        _f(id="f_bad", severity="high", file="not_in_diff.py", start_line=99, end_line=99),
    ]
    first_response = {
        "_error": "HTTP 422: ...",
        "_error_kind": "unresolved_anchor",
        "_raw_errors": ["Path could not be resolved"],
        "_status": 422,
    }
    retry_response = {"id": 9999}
    post_review = AsyncMock(side_effect=[first_response, retry_response])

    pr_diff = (
        "diff --git a/in_diff.py b/in_diff.py\n"
        "--- a/in_diff.py\n"
        "+++ b/in_diff.py\n"
        "@@ -1,1 +10,1 @@\n"
        "+touched\n"
    )

    with (
        patch(
            "agent.tools.publish_review.get_config",
            return_value={"configurable": {"thread_id": "tid"}},
        ),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch(
            "agent.tools.publish_review.list_findings_async",
            AsyncMock(return_value=findings),
        ),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch(
            "agent.tools.publish_review.fetch_pr_diff",
            AsyncMock(return_value=pr_diff),
        ),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "agent.tools.publish_review._store_thread_ids_on_findings",
            new_callable=AsyncMock,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
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

    assert post_review.await_count == 2
    retry_inline = post_review.await_args_list[1].kwargs["inline_comments"]
    assert {c["path"] for c in retry_inline} == {"in_diff.py"}
    assert result["success"] is True
    assert result["unresolvable_findings"] == ["f_bad"]
