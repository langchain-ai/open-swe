"""Unit tests for the publish_review rendering and orchestration helpers."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.dashboard.workspace_settings import WorkspaceSettings
from agent.review.assessment_feedback import ASSESSMENTS
from agent.review.findings import Finding, new_finding
from agent.review.publish import (
    ReviewAssessment,
    post_pull_request_review,
    render_inline_comment_body,
)
from tests.conftest import FakeStore


def _assessment(head_sha: str = "a" * 40) -> ReviewAssessment:
    return ReviewAssessment(
        head_sha=head_sha,
        risk_score=1,
        decision="would_approve",
        explanation="Documentation only; satisfies the repository approval instructions.",
    )


async def test_stale_assessment_does_not_publish_or_advance_reviewed_commit() -> None:
    from agent.tools.publish_review import _publish_review_async

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch(
            "agent.tools.publish_review.resolve_review_head_sha", AsyncMock(return_value="b" * 40)
        ),
        patch("agent.tools.publish_review.post_pull_request_review", AsyncMock()) as post,
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()) as metadata,
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="a" * 40,
            token="t",
            severity_threshold="medium",
            is_re_review=False,
            assessment=_assessment(),
            state={"review_approval_policy": "Docs only"},
        )
    assert result["success"] is False
    assert "commit" in result["error"]
    post.assert_not_awaited()
    metadata.assert_not_awaited()


def _f(**overrides: Any) -> Finding:
    construct_keys = {
        "severity",
        "confidence",
        "category",
        "file",
        "start_line",
        "end_line",
        "description",
        "sha",
        "title",
        "side",
        "suggestion",
        "diff_hunk",
        "finding_id",
        "in_diff",
    }
    kwargs: dict[str, Any] = {
        "severity": "high",
        "confidence": "high",
        "category": "correctness",
        "file": "src/foo.py",
        "start_line": 10,
        "end_line": 10,
        "description": "boom",
        "sha": "abc",
    }
    rest: dict[str, Any] = {}
    for key, value in overrides.items():
        if key == "id":
            kwargs["finding_id"] = value
        elif key in construct_keys:
            kwargs[key] = value
        else:
            rest[key] = value
    finding = new_finding(**kwargs)
    if rest:
        finding.update(rest)  # type: ignore[typeddict-item]
    return finding


@pytest.fixture(autouse=True)
def _isolate_publish_review_pr_state(fake_store: FakeStore) -> Iterator[None]:
    with (
        patch(
            "agent.tools.publish_review.get_workspace_settings",
            AsyncMock(return_value=WorkspaceSettings({})),
        ),
        patch("agent.tools.publish_review.PullRequest.link_review", AsyncMock()),
        patch("agent.tools.publish_review.fetch_pr_review_threads", AsyncMock(return_value=[])),
        patch("agent.tools.publish_review.replace_findings", AsyncMock()),
        patch("agent.tools.publish_review.open_swe_review_exists", AsyncMock(return_value=False)),
        patch("agent.tools.publish_review.clear_review_started_comment", AsyncMock()),
        patch(
            "agent.tools.publish_review.resolve_review_head_sha",
            AsyncMock(
                side_effect=lambda thread_id, configurable: configurable.get("head_sha") or ""
            ),
        ),
    ):
        yield


def _eval_config(**configurable: object) -> dict[str, object]:
    return {
        "configurable": {
            "thread_id": "tid",
            "repo": {"owner": "o", "name": "r"},
            "pr_number": 7,
            "head_sha": "sha",
            "reviewer_eval": True,
            **configurable,
        },
        "metadata": {},
    }


@pytest.mark.parametrize(
    ("ranking", "missing", "unknown", "duplicates"),
    [
        (["f_one"], ["f_two"], [], []),
        (["f_one", "f_two", "f_nope"], [], ["f_nope"], []),
        (["f_one", "f_two", "f_one"], [], [], ["f_one"]),
    ],
)
async def test_publish_review_rejects_a_ranking_that_is_not_a_total_order(
    ranking: list[str], missing: list[str], unknown: list[str], duplicates: list[str]
) -> None:
    from agent.tools.publish_review import publish_review

    findings = [
        _f(id="f_one", severity="high", file="a.py", start_line=1, end_line=1),
        _f(id="f_two", severity="low", file="b.py", start_line=2, end_line=2),
    ]
    with (
        patch("agent.tools.publish_review.get_config", return_value=_eval_config()),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.tools.publish_review.mutate_findings", AsyncMock()) as mutate,
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()) as set_meta,
    ):
        result = await publish_review(ranking=ranking)

    assert result["success"] is False
    assert result["expected_finding_ids"] == ["f_one", "f_two"]
    assert (result["missing"], result["unknown"], result["duplicates"]) == (
        missing,
        unknown,
        duplicates,
    )
    mutate.assert_not_awaited()
    set_meta.assert_not_awaited()


async def test_publish_review_refuses_when_called_alongside_other_tools() -> None:
    from langchain_core.messages import AIMessage

    from agent.tools.publish_review import publish_review

    turn = AIMessage(
        "",
        tool_calls=[
            {"name": "add_finding", "args": {}, "id": "call_add"},
            {"name": "publish_review", "args": {"ranking": []}, "id": "call_publish"},
        ],
    )
    with patch("agent.tools.publish_review._record_ranking", AsyncMock()) as record_ranking:
        result = await publish_review(ranking=[], state={"messages": [turn]})

    assert result["success"] is False
    record_ranking.assert_not_awaited()


async def test_eval_run_publishes_the_findings_it_recorded_without_postgres() -> None:
    from agent.review.findings import (
        REVIEWER_EVAL_PUBLICATION_KEY,
        append_finding,
        start_run_scoped_findings,
    )
    from agent.tools.publish_review import publish_review

    start_run_scoped_findings("tid")
    await append_finding(
        "tid", _f(id="f_one", severity="high", file="a.py", start_line=1, end_line=1)
    )
    with (
        patch("agent.tools.publish_review.get_config", return_value=_eval_config()),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()) as set_meta,
    ):
        result = await publish_review(ranking=["f_one"])

    assert result["surfaced_count"] == 1
    publication = set_meta.await_args.kwargs["extra"][REVIEWER_EVAL_PUBLICATION_KEY]
    assert [finding["id"] for finding in publication["findings"]] == ["f_one"]


@pytest.mark.asyncio
async def test_advisory_summary_updates_latest_commented_review() -> None:
    from agent.review.publish import review_summary_marker

    marker = review_summary_marker(1)
    listed = MagicMock()
    listed.json.return_value = [
        {"id": 10, "state": "COMMENTED", "body": marker},
        {"id": 11, "state": "COMMENTED", "body": marker},
        {"id": 12, "state": "APPROVED", "body": marker},
    ]
    updated = MagicMock()
    updated.json.return_value = {"id": 11, "body": f"Updated assessment {marker}"}
    with patch(
        "agent.review.publish.github_request", AsyncMock(side_effect=[listed, updated])
    ) as request:
        result = await post_pull_request_review(
            owner="o",
            repo="r",
            pr_number=1,
            head_sha="new-sha",
            body=f"Updated assessment {marker}",
            inline_comments=[],
            token="t",
        )
    assert result == {"id": 11, "body": f"Updated assessment {marker}"}
    assert [call.args[1] for call in request.await_args_list] == ["GET", "PUT"]
    assert request.await_args_list[-1].args[2].endswith("/reviews/11")


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

    with patch("agent.github.http.httpx2.AsyncClient", return_value=client_cm):
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
async def test_publish_review_skips_findings_already_published() -> None:
    """Re-runs must not re-post findings that already carry a review comment id."""
    from agent.tools.publish_review import _publish_review_async

    findings = [
        _f(id="f_old", severity="high", file="a.py", github_review_comment_ids=[42]),
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
            is_re_review=False,
        )

    assert result["success"] is True
    assert result["surfaced_count"] == 1
    assert post_review.await_args is not None
    posted = post_review.await_args.kwargs["inline_comments"]
    paths = {c["path"] for c in posted}
    assert paths == {"b.py"}


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment_mode", [None, "dry_run", "approve"])
async def test_published_review_registry_failure_does_not_complete_or_invite_duplicate(
    assessment_mode: str | None,
) -> None:
    from agent.tools.publish_review import _publish_review_async

    with (
        patch(
            "agent.tools.publish_review.approval_mode_for", AsyncMock(return_value=assessment_mode)
        ),
        patch("agent.tools.publish_review.approval_allowed_for_head", AsyncMock(return_value=True)),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review.open_swe_review_exists",
            AsyncMock(side_effect=[False, True]),
        ),
        patch(
            "agent.tools.publish_review.post_pull_request_review",
            AsyncMock(return_value={"id": 999}),
        ) as post,
        patch(
            "agent.tools.publish_review.PullRequest.link_review",
            AsyncMock(side_effect=[RuntimeError("Storage unavailable"), None]),
        ) as completion,
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            AsyncMock(return_value=0),
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()) as metadata,
        patch("agent.tools.publish_review.settle_review_check_run", AsyncMock()) as settle,
        patch(
            "agent.tools.publish_review._maybe_post_slack_completion_reply", AsyncMock()
        ) as notify,
        patch("agent.tools.publish_review._record_reviewer_usage", AsyncMock()),
    ):

        async def publish() -> dict[str, object]:
            return await _publish_review_async(
                owner="o",
                repo="r",
                pr_number=7,
                head_sha="a" * 40,
                token="t",
                severity_threshold="medium",
                is_re_review=False,
                assessment=_assessment("a" * 40) if assessment_mode else None,
                state={"review_approval_policy": "Docs only"},
            )

        if assessment_mode:
            result = await publish()
            assert result["success"] is True
            assert result["review_id"] == 999
            assert result["completion_recorded"] is False
            assert "merge remains blocked" in str(result["warning"])
            post.assert_awaited_once()
            assert post.await_args is not None
            assert post.await_args.kwargs["event"] == (
                "APPROVE" if assessment_mode == "approve" else "COMMENT"
            )
        else:
            with pytest.raises(RuntimeError, match="Storage unavailable"):
                await publish()
        completion.assert_awaited_once_with(
            reviewer_thread_id="tid",
            github_review_id=999,
            head_sha="a" * 40,
            finding_count=0,
        )
        assert not any("last_reviewed_sha" in call.kwargs for call in metadata.await_args_list)
        settle.assert_not_awaited()
        notify.assert_not_awaited()

        if assessment_mode:
            return
        result = await publish()
        assert result["success"] is True
        assert result["skipped_empty_re_review"] is True
        post.assert_awaited_once()
        metadata.assert_awaited_once_with("tid", last_reviewed_sha="a" * 40)
        settle.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_fails", [False, True])
async def test_publish_review_skips_post_on_re_review_with_no_new_findings(
    storage_fails: bool,
) -> None:
    """Re-review with nothing new to surface must not spam another comment."""
    from agent.tools.publish_review import _publish_review_async

    # All findings already carry a review comment id from the prior publish
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
            "github_review_comment_ids": [100],
        },
    ]
    list_async = AsyncMock(return_value=findings)
    post_review = AsyncMock()
    set_metadata = AsyncMock()
    resolve_threads = AsyncMock(return_value=1)
    completion = AsyncMock(
        side_effect=RuntimeError("Storage unavailable") if storage_fails else None
    )
    settle_check = AsyncMock()

    with (
        patch("agent.tools.publish_review.PullRequest.link_review", completion),
        patch("agent.tools.publish_review.settle_review_check_run", settle_check),
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

        async def publish() -> dict[str, object]:
            return await _publish_review_async(
                owner="o",
                repo="r",
                pr_number=7,
                head_sha="newsha",
                token="t",
                severity_threshold="medium",
                is_re_review=True,
            )

        if storage_fails:
            with pytest.raises(RuntimeError, match="Storage unavailable"):
                await publish()
            set_metadata.assert_not_awaited()
            settle_check.assert_not_awaited()
            post_review.assert_not_awaited()
            return
        result = await publish()

    completion.assert_awaited_once_with(
        reviewer_thread_id="tid", head_sha="newsha", finding_count=0
    )
    settle_check.assert_awaited_once()
    assert settle_check.await_args is not None
    assert settle_check.await_args.kwargs["conclusion"] == "success"
    post_review.assert_not_called()
    resolve_threads.assert_awaited_once()
    set_metadata.assert_awaited_once()
    assert result["success"] is True
    assert result["review_id"] is None
    assert result["surfaced_count"] == 0
    assert result["resolved_thread_count"] == 1
    assert result["skipped_empty_re_review"] is True


@pytest.mark.asyncio
async def test_publish_review_does_not_surface_out_of_diff_finding() -> None:
    """Out-of-diff findings are disabled: a finding anchored outside the diff is
    never surfaced on the PR. On a re-review with nothing else to post, it is
    treated as an empty re-review."""
    from agent.tools.publish_review import _publish_review_async

    findings = [
        _f(
            id="f_ood",
            file="caller.py",
            in_diff=False,
            first_seen_sha="newsha",
        )
    ]
    post_review = AsyncMock(return_value={"id": 555})

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch(
            "agent.tools.publish_review._open_swe_already_reviewed",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            AsyncMock(return_value=0),
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()),
        patch("agent.tools.publish_review.clear_review_started_comment", AsyncMock()),
        patch("agent.tools.publish_review.settle_review_check_run", AsyncMock()),
        patch("agent.tools.publish_review._maybe_post_slack_completion_reply", AsyncMock()),
        patch("agent.tools.publish_review._resolve_review_trace_url", AsyncMock(return_value=None)),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="newsha",
            token="t",
            severity_threshold="medium",
            is_re_review=True,
        )

    post_review.assert_not_called()
    assert result["success"] is True
    assert result["review_id"] is None
    assert result["surfaced_count"] == 0
    assert "out_of_diff_count" not in result
    assert result["skipped_empty_re_review"] is True


@pytest.mark.asyncio
async def test_publish_review_dedup_keys_off_durable_last_reviewed_sha() -> None:
    """A non-empty ``last_reviewed_sha`` on thread metadata means this thread
    already published once. The empty-summary guard must trust that durable
    signal and suppress without ever hitting the reviews API."""
    from agent.tools.publish_review import _publish_review_async

    review_exists = AsyncMock(return_value=False)
    post_review = AsyncMock()

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review.get_thread_metadata",
            AsyncMock(return_value={"last_reviewed_sha": "oldsha"}),
        ),
        patch("agent.tools.publish_review.open_swe_review_exists", review_exists),
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
            head_sha="newsha",
            token="t",
            severity_threshold="medium",
            is_re_review=False,
        )

    review_exists.assert_not_called()
    post_review.assert_not_called()
    assert result["skipped_empty_re_review"] is True


@pytest.mark.asyncio
async def test_re_review_backfills_and_resolves_duplicate_existing_threads() -> None:
    from agent.tools.publish_review import _publish_review_async

    finding = _f(
        id="f_old",
        first_seen_sha="oldsha",
        status="resolved",
        resolution_note="The duplicate threads are fixed by the latest commit.",
    )
    findings = [finding]
    threads = [
        {
            "id": "THREAD_1",
            "is_resolved": False,
            "is_outdated": False,
            "comments": [
                {
                    "id": 101,
                    "author": "open-swe[bot]",
                    "body": render_inline_comment_body(finding),
                    "created_at": "2026-05-27T10:00:00Z",
                }
            ],
        },
        {
            "id": "THREAD_2",
            "is_resolved": False,
            "is_outdated": False,
            "comments": [
                {
                    "id": 102,
                    "author": "open-swe[bot]",
                    "body": render_inline_comment_body(finding),
                    "created_at": "2026-05-27T10:01:00Z",
                }
            ],
        },
    ]
    resolve_thread = AsyncMock(return_value=True)
    reply_comment = AsyncMock(return_value={"id": 555})

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch(
            "agent.tools.publish_review.fetch_pr_review_threads", AsyncMock(return_value=threads)
        ),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.review.reconcile.list_findings", AsyncMock(return_value=findings)),
        patch("agent.review.reconcile.replace_findings", AsyncMock()),
        patch("agent.tools.publish_review.post_pull_request_review", AsyncMock()),
        patch("agent.tools.publish_review.resolve_review_thread", resolve_thread),
        patch(
            "agent.tools.publish_review.fetch_review_thread_id_for_comment",
            AsyncMock(return_value=None),
        ),
        patch("agent.tools.publish_review.reply_to_review_comment", reply_comment),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="newsha",
            token="t",
            severity_threshold="medium",
            is_re_review=True,
        )

    assert result["success"] is True
    assert result["review_id"] is None
    assert result["resolved_thread_count"] == 2
    assert resolve_thread.await_count == 2
    assert reply_comment.await_count == 2
    assert (
        reply_comment.await_args_list[0].kwargs["body"]
        == "The duplicate threads are fixed by the latest commit."
    )
    assert findings[0]["github_review_comment_ids"] == [101, 102]
    assert findings[0]["github_review_thread_ids"] == ["THREAD_1", "THREAD_2"]
    assert findings[0]["github_resolved_thread_ids"] == ["THREAD_1", "THREAD_2"]
    assert findings[0]["github_posted_resolution_comment_ids"] == [101, 102]
    assert findings[0]["surface_state"] == "resolved"


@pytest.mark.asyncio
async def test_publish_review_backfills_from_threads_when_review_comments_are_empty() -> None:
    from agent.tools.publish_review import _publish_review_async

    finding = _f(id="f_new", first_seen_sha="sha")
    findings = [finding]
    thread = {
        "id": "THREAD_1",
        "is_resolved": False,
        "is_outdated": False,
        "comments": [
            {
                "id": 202,
                "author": "open-swe[bot]",
                "body": render_inline_comment_body(finding),
                "created_at": "2026-05-27T10:00:00Z",
            }
        ],
    }
    fetch_threads = AsyncMock(side_effect=[[], [thread]])

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.fetch_pr_review_threads", fetch_threads),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.review.reconcile.list_findings", AsyncMock(return_value=findings)),
        patch("agent.review.reconcile.replace_findings", AsyncMock()),
        patch(
            "agent.tools.publish_review.post_pull_request_review",
            AsyncMock(return_value={"id": 999}),
        ),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
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
            is_re_review=False,
        )

    assert result["success"] is True
    assert result["review_id"] == 999
    assert fetch_threads.await_count == 2
    assert findings[0]["github_review_id"] == 999
    assert findings[0]["github_review_comment_ids"] == [202]
    assert findings[0]["github_review_thread_ids"] == ["THREAD_1"]


@pytest.mark.asyncio
async def test_re_review_only_posts_current_head_unpublished_findings() -> None:
    from agent.tools.publish_review import _publish_review_async

    old = _f(id="f_old", first_seen_sha="oldsha", file="old.py")
    new = _f(id="f_new", first_seen_sha="newsha", file="new.py")
    findings = [old, new]
    post_review = AsyncMock(return_value={"id": 888})
    fetch_comments = AsyncMock(
        return_value=[
            {
                "id": 303,
                "path": "new.py",
                "line": 10,
                "body": render_inline_comment_body(new),
            }
        ]
    )

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.fetch_review_comments", fetch_comments),
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
            head_sha="newsha",
            token="t",
            severity_threshold="medium",
            is_re_review=True,
        )

    assert result["success"] is True
    assert result["surfaced_count"] == 1
    assert post_review.await_args is not None
    inline_comments = post_review.await_args.kwargs["inline_comments"]
    assert [comment["path"] for comment in inline_comments] == ["new.py"]
    assert old["github_review_id"] is None
    assert new["github_review_id"] == 888
    assert new["github_review_comment_ids"] == [303]


@pytest.mark.asyncio
async def test_publish_review_matches_comment_ids_by_marker_not_path_line_body() -> None:
    """Two findings on the same path/line with identical rendered bodies must
    each get their OWN comment id, matched via the embedded marker. The old
    ``(path, line, body)`` fallback collided here and cached one comment id on
    both findings, breaking resolve-on-fix."""
    from agent.tools.publish_review import _publish_review_async

    f1 = _f(id="f_one", file="dup.py", start_line=5, end_line=5, description="same text")
    f2 = _f(id="f_two", file="dup.py", start_line=5, end_line=5, description="same text")
    findings = [f1, f2]
    post_review = AsyncMock(return_value={"id": 700})
    # GitHub returns one comment per finding; the only thing that distinguishes
    # them is the marker embedded in each body.
    fetch_comments = AsyncMock(
        return_value=[
            {"id": 901, "path": "dup.py", "line": 5, "body": render_inline_comment_body(f1)},
            {"id": 902, "path": "dup.py", "line": 5, "body": render_inline_comment_body(f2)},
        ]
    )

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.fetch_review_comments", fetch_comments),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review._store_thread_ids_on_findings", new_callable=AsyncMock),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch("agent.tools.publish_review._maybe_post_slack_completion_reply", AsyncMock()),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="sha",
            token="t",
            severity_threshold="medium",
            is_re_review=False,
        )

    assert result["success"] is True
    by_id = {f["id"]: f for f in findings}
    assert by_id["f_one"]["github_review_comment_ids"] == [901]
    assert by_id["f_two"]["github_review_comment_ids"] == [902]


@pytest.mark.asyncio
async def test_publish_review_drops_unresolvable_findings_and_retries_once(
    fake_store: FakeStore,
) -> None:
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
            "agent.run_config.get_config",
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
            head_sha="a" * 40,
            token="t",
            severity_threshold="medium",
            is_re_review=False,
            assessment=_assessment(),
            state={"review_approval_policy": "Docs only"},
        )

    assert post_review.await_count == 2
    assert "Risk: 1/5" in post_review.await_args_list[0].kwargs["body"]
    assert "Risk:" not in post_review.await_args_list[1].kwargs["body"]
    # Retry must contain only the in-diff finding.
    retry_inline = post_review.await_args_list[1].kwargs["inline_comments"]
    assert {c["path"] for c in retry_inline} == {"in_diff.py"}
    assert result["success"] is True
    assert result["review_id"] == 7777
    assert await ASSESSMENTS.get("7777") is None
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
            "agent.run_config.get_config",
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
            "agent.run_config.get_config",
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
            is_re_review=False,
        )

    # Only one attempt — never retry blindly.
    assert post_review.await_count == 1
    assert result["success"] is False
    assert result["unresolvable_findings"] == []
    assert "update_finding" in result["hint"]


async def test_publish_review_tool_returns_structured_error_when_thread_missing() -> None:
    """A missing reviewer thread surfaces as a do-not-retry tool result instead
    of an exception the middleware swallows into an empty tool message."""
    from agent.review.findings import ReviewerThreadMissingError
    from agent.tools.publish_review import publish_review

    publish_async = AsyncMock(
        side_effect=ReviewerThreadMissingError("tid", RuntimeError("thread tid not found"))
    )
    with (
        patch(
            "agent.tools.publish_review.get_config",
            return_value={
                "configurable": {
                    "thread_id": "tid",
                    "repo": {"owner": "o", "name": "r"},
                    "pr_number": 7,
                    "head_sha": "sha",
                },
                "metadata": {},
            },
        ),
        patch("agent.tools.publish_review.resolve_thread_github_token", return_value="token"),
        patch("agent.tools.publish_review._publish_review_async", publish_async),
        patch("agent.tools.publish_review._record_ranking", AsyncMock(return_value=None)),
    ):
        result = await publish_review(ranking=[])

    assert result["success"] is False
    assert result["error"] == "thread_not_found"
    assert result["thread_id"] == "tid"
    assert "Do not retry" in result["note"]


@pytest.mark.parametrize(
    "prepared_policy,mode,current_head,expected_event,has_assessment",
    [
        (None, "approve", True, "COMMENT", False),
        ("Docs only", "off", True, "COMMENT", False),
        ("Docs only", "dry_run", True, "COMMENT", True),
        ("Docs only", "approve", False, "COMMENT", True),
        ("Docs only", "approve", True, "APPROVE", True),
    ],
    ids=["no-policy", "switched-off", "dry-run", "head-moved", "approve"],
)
async def test_publication_respects_the_base_policy_and_current_mode(
    prepared_policy: str | None,
    mode: str,
    current_head: bool,
    expected_event: str,
    has_assessment: bool,
) -> None:
    from agent.tools.publish_review import _publish_review_async

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.approval_mode_for", AsyncMock(return_value=mode)),
        patch(
            "agent.tools.publish_review.approval_allowed_for_head",
            AsyncMock(return_value=current_head),
        ),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review.post_pull_request_review",
            AsyncMock(return_value={"id": 77}),
        ) as post,
        patch("agent.tools.publish_review._resolve_review_trace_url", AsyncMock(return_value=None)),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            AsyncMock(return_value=0),
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()),
        patch("agent.tools.publish_review._record_reviewer_usage", AsyncMock()),
        patch("agent.tools.publish_review.settle_review_check_run", AsyncMock()),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="a" * 40,
            token="t",
            severity_threshold="medium",
            is_re_review=False,
            assessment=_assessment(),
            state={"review_approval_policy": prepared_policy},
        )
    assert result["success"] is True
    assert post.await_args is not None
    assert post.await_args.kwargs["event"] == expected_event
    body = post.await_args.kwargs["body"]
    assert ("Risk:" in body) is has_assessment
    assert ("(dry run)" in body) is (has_assessment and mode == "dry_run")
    saved = await ASSESSMENTS.get("77")
    assert (saved is not None) is has_assessment
    if saved is not None:
        assert saved.dry_run is (mode == "dry_run")
        assert saved.approved is (expected_event == "APPROVE")


@pytest.mark.parametrize(
    "pr",
    [
        {"state": "open", "draft": False, "head": {"sha": "b" * 40}},
        {"state": "open", "draft": True, "head": {"sha": "a" * 40}},
        {"state": "closed", "draft": False, "head": {"sha": "a" * 40}},
        {},
    ],
)
async def test_approval_rechecks_github_head_and_pr_state(pr: dict[str, object]) -> None:
    from agent.review.publish import approval_allowed_for_head

    response = MagicMock()
    response.json.return_value = pr
    with patch("agent.review.publish.github_request", AsyncMock(return_value=response)):
        assert not await approval_allowed_for_head(
            owner="o", repo="r", pr_number=7, head_sha="a" * 40, token="t"
        )


async def test_approved_review_posts_approve_event_for_reviewed_commit() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"id": 77, "state": "APPROVED"}
    with patch("agent.review.publish.github_request", AsyncMock(return_value=response)) as request:
        await post_pull_request_review(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="a" * 40,
            token="t",
            body="Approved",
            inline_comments=[],
            event="APPROVE",
        )
    assert request.await_args is not None
    payload = request.await_args.kwargs["json"]
    assert payload["event"] == "APPROVE"
    assert payload["commit_id"] == "a" * 40


@pytest.mark.parametrize(
    "status,error_body,retry,succeeds",
    [
        (422, {"errors": ["Can not approve your own pull request"]}, True, True),
        (422, {"message": "Can not approve your own pull request"}, True, True),
        (422, {"errors": [{"message": "Can not approve your own pull request"}]}, True, True),
        (422, {"errors": ["Review body is too long"]}, False, False),
        (500, {"message": "Can not approve your own pull request"}, False, False),
        (422, {"errors": ["Can not approve your own pull request"]}, True, False),
    ],
)
async def test_approval_publication_handles_github_rejections(
    status: int, error_body: dict[str, object], retry: bool, succeeds: bool
) -> None:
    import httpx2

    from agent.tools.publish_review import _publish_review_async

    request = httpx2.Request("POST", "https://api.github.com/repos/o/r/pulls/7/reviews")
    responses = [httpx2.Response(status, json=error_body, request=request)]
    if retry:
        responses.append(httpx2.Response(200, json=[], request=request))
        responses.append(
            httpx2.Response(
                200 if succeeds else 422,
                json={"id": 77} if succeeds else error_body,
                request=request,
            )
        )
    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.approval_mode_for", AsyncMock(return_value="approve")),
        patch("agent.tools.publish_review.approval_allowed_for_head", AsyncMock(return_value=True)),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch("agent.review.publish.github_request", AsyncMock(side_effect=responses)) as post,
        patch("agent.tools.publish_review._resolve_review_trace_url", AsyncMock(return_value=None)),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            AsyncMock(return_value=0),
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()) as metadata,
        patch("agent.tools.publish_review._record_reviewer_usage", AsyncMock()),
        patch("agent.tools.publish_review.settle_review_check_run", AsyncMock()) as settle_check,
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="a" * 40,
            token="t",
            severity_threshold="medium",
            is_re_review=False,
            assessment=_assessment(),
            state={"review_approval_policy": "Docs only"},
        )
    assert result["success"] is succeeds
    payloads = [call.kwargs["json"] for call in post.await_args_list if call.args[1] == "POST"]
    assert [payload["event"] for payload in payloads] == (
        ["APPROVE", "COMMENT"] if retry else ["APPROVE"]
    )
    if retry:
        assert payloads[1]["commit_id"] == "a" * 40
        assert "Would approve" in payloads[1]["body"]
        assert "Approved" not in payloads[1]["body"]
    saved = await ASSESSMENTS.get("77")
    if succeeds:
        assert saved is not None
        assert saved.approved is False
        assert saved.decision == "would_approve"
        assert saved.head_sha == "a" * 40
        metadata.assert_any_await("tid", last_reviewed_sha="a" * 40)
        settle_check.assert_awaited_once()
    else:
        assert saved is None
        assert "Failed to POST PR review" in result["error"]
        metadata.assert_not_awaited()
        settle_check.assert_not_awaited()


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
            "agent.run_config.get_config",
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
            is_re_review=False,
        )

    assert post_review.await_count == 2
    retry_inline = post_review.await_args_list[1].kwargs["inline_comments"]
    assert {c["path"] for c in retry_inline} == {"in_diff.py"}
    assert result["success"] is True
    assert result["unresolvable_findings"] == ["f_bad"]
