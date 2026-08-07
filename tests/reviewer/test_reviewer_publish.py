"""Unit tests for the publish_review rendering and orchestration helpers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.review.findings import Finding, new_finding
from agent.review.publish import (
    clear_review_started_comment,
    fetch_pr_review_threads,
    open_swe_review_exists,
    parse_review_comment_marker,
    post_pull_request_review,
    post_review_started_comment,
    render_inline_comment_body,
    render_inline_comment_payload,
    render_resolution_comment,
    render_review_body,
    render_status_comment,
    reply_to_review_comment,
    resolve_review_thread,
    review_summary_marker,
    status_comment_marker,
)


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
def _isolate_publish_review_pr_state() -> Iterator[None]:
    with (
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


def test_render_inline_comment_body_without_suggestion() -> None:
    body = render_inline_comment_body(_f(description="just text"))
    assert "<!-- open-swe-review-comment" in body
    assert '"id":"f_' in body
    assert "just text" in body
    assert "Your feedback helps Open SWE learn." in body
    assert "👍 or 👎" in body
    assert "tell us if this review comment was useful" in body


def test_render_inline_comment_body_with_suggestion_appends_block() -> None:
    body = render_inline_comment_body(
        _f(description="needs fix", suggestion="x = 1\nx += 1"),
    )
    assert "needs fix" in body
    assert "```suggestion" in body
    assert "x = 1\nx += 1" in body


def test_render_inline_comment_body_uses_severity_emoji_and_bold_title() -> None:
    body = render_inline_comment_body(_f(severity="critical", description="Null deref"))
    assert "🔴 **Null deref**" in body


def test_render_inline_comment_body_uses_generated_title() -> None:
    description = "This request can fail because the new path skips auth token refresh."
    body = render_inline_comment_body(_f(title="Refresh token skipped", description=description))

    assert "🟠 **Refresh token skipped**" in body
    assert description in body
    assert "This request can fail because the new path skips auth token refresh" in body


def test_render_inline_comment_body_does_not_duplicate_first_line() -> None:
    body = render_inline_comment_body(
        _f(description="Short summary line\n\nLonger detail paragraph."),
    )
    assert "**Short summary line**" in body
    assert "Longer detail paragraph." in body
    assert body.count("Short summary line") == 1


def test_render_inline_comment_body_does_not_duplicate_generated_title() -> None:
    body = render_inline_comment_body(
        _f(
            title="Short summary line", description="Short summary line\n\nLonger detail paragraph."
        ),
    )
    assert "**Short summary line**" in body
    assert "Longer detail paragraph." in body
    assert body.count("Short summary line") == 1


def test_render_inline_comment_body_single_line_has_no_detail() -> None:
    body = render_inline_comment_body(_f(description="just text"))
    assert body.count("just text") == 1


def test_render_inline_comment_body_line_reference_range() -> None:
    assert "*(Refers to lines 10-12)*" in render_inline_comment_body(_f(start_line=10, end_line=12))
    assert "*(Refers to line 10)*" in render_inline_comment_body(_f(start_line=10, end_line=10))


def test_render_resolution_comment_resolved_uses_note_verbatim() -> None:
    body = render_resolution_comment(_f(status="resolved"), "resolved", note="Fixed at line 5")
    assert body == "Fixed at line 5"


def test_render_resolution_comment_returns_none_without_agent_note() -> None:
    body = render_resolution_comment(_f(status="resolved"), "resolved")
    assert body is None


def test_render_resolution_comment_dismissed_uses_note_verbatim() -> None:
    body = render_resolution_comment(_f(status="dismissed"), "dismissed", note="Intended behavior")
    assert body == "Intended behavior"


def test_render_resolution_comment_uses_stored_resolution_note_verbatim() -> None:
    finding = _f(status="resolved", resolution_note="The guard now returns before indexing.")
    body = render_resolution_comment(finding, "resolved")
    assert body == "The guard now returns before indexing."


def test_parse_review_comment_marker_accepts_valid_marker() -> None:
    finding = _f(
        id="f_marker",
        file="agent/webapp.py",
        start_line=10,
        end_line=12,
        side="RIGHT",
    )
    marker = parse_review_comment_marker(render_inline_comment_body(finding))

    assert marker == {
        "id": "f_marker",
        "file_path": "agent/webapp.py",
        "start_line": 10,
        "end_line": 12,
        "side": "RIGHT",
    }


def test_parse_review_comment_marker_rejects_malformed_marker() -> None:
    assert parse_review_comment_marker("plain body") is None
    assert parse_review_comment_marker("<!-- open-swe-review-comment {} -->") is None
    assert (
        parse_review_comment_marker(
            '<!-- open-swe-review-comment {"id":"f1","file_path":"x.py","side":"BAD"} -->'
        )
        is None
    )


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
    assert "additional" not in body


def test_render_review_body_with_additional_findings_and_ui_link() -> None:
    body = render_review_body(
        pr_number=99,
        surfaced_count=0,
        additional_findings_count=2,
        ui_url="https://dash.example/agents/reviews/o/r/99",
    )
    assert "## ✅ Open SWE Review: No issues found" in body
    assert "2 additional findings can be viewed in the web app." in body
    assert "[Open in Web](https://dash.example/agents/reviews/o/r/99)" in body


def test_render_review_body_with_single_additional_finding_uses_singular() -> None:
    body = render_review_body(pr_number=99, surfaced_count=0, additional_findings_count=1)
    assert "1 additional finding can be viewed in the web app." in body


def test_render_review_body_with_surfaced_and_additional_findings() -> None:
    body = render_review_body(
        pr_number=99,
        surfaced_count=3,
        additional_findings_count=2,
        ui_url="https://dash.example/agents/reviews/o/r/99",
    )
    assert "found 3 potential issues." in body
    assert "2 additional findings can be viewed in the web app." in body


def test_render_review_body_additional_findings_zero_omits_line() -> None:
    body = render_review_body(pr_number=99, surfaced_count=0, additional_findings_count=0)
    assert "additional" not in body


def test_render_status_comment_reviewing_includes_ui_link(monkeypatch: Any) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dash.example")
    body = render_status_comment(pr_number=7, thread_id="tid-1")
    assert "🔍 Open SWE Review: in progress" in body
    assert "[Open in Web](https://dash.example/agents/tid-1)" in body
    assert status_comment_marker(7) in body


def test_render_review_body_includes_ui_link() -> None:
    body = render_review_body(
        pr_number=7, surfaced_count=0, ui_url="https://dash.example/agents/tid-1"
    )
    assert "[Open in Web](https://dash.example/agents/tid-1)" in body


def test_render_review_body_orders_ui_link_before_trace() -> None:
    body = render_review_body(
        pr_number=7,
        surfaced_count=1,
        ui_url="https://dash.example/agents/tid-1",
        trace_url="https://trace.example/x",
    )
    assert "[Open in Web](https://dash.example/agents/tid-1) • [View Open SWE trace]" in body


@pytest.mark.asyncio
async def test_post_review_started_comment_posts_and_persists_id() -> None:
    post = AsyncMock(return_value=4242)
    set_meta = AsyncMock()
    with (
        patch("agent.review.publish.get_thread_metadata", AsyncMock(return_value={})),
        patch("agent.review.publish.post_status_comment", post),
        patch("agent.review.publish.delete_status_comment", AsyncMock()) as delete,
        patch("agent.review.publish.set_reviewer_thread_metadata", set_meta),
    ):
        cid = await post_review_started_comment(
            thread_id="tid", owner="o", repo="r", pr_number=7, token="t"
        )
    assert cid == 4242
    delete.assert_not_called()
    post.assert_awaited_once()
    set_meta.assert_awaited_once_with("tid", extra={"status_comment_id": 4242})


@pytest.mark.asyncio
async def test_post_review_started_comment_deletes_lingering_before_reposting() -> None:
    post = AsyncMock(return_value=500)
    delete = AsyncMock(return_value=True)
    with (
        patch(
            "agent.review.publish.get_thread_metadata",
            AsyncMock(return_value={"status_comment_id": 99}),
        ),
        patch("agent.review.publish.post_status_comment", post),
        patch("agent.review.publish.delete_status_comment", delete),
        patch("agent.review.publish.set_reviewer_thread_metadata", AsyncMock()),
    ):
        cid = await post_review_started_comment(
            thread_id="tid", owner="o", repo="r", pr_number=7, token="t"
        )
    assert cid == 500
    delete.assert_awaited_once()
    assert delete.await_args is not None
    assert delete.await_args.kwargs["comment_id"] == 99


@pytest.mark.asyncio
async def test_clear_review_started_comment_deletes_and_clears_metadata() -> None:
    delete = AsyncMock(return_value=True)
    set_meta = AsyncMock()
    with (
        patch(
            "agent.review.publish.get_thread_metadata",
            AsyncMock(return_value={"status_comment_id": 99}),
        ),
        patch("agent.review.publish.delete_status_comment", delete),
        patch("agent.review.publish.set_reviewer_thread_metadata", set_meta),
    ):
        await clear_review_started_comment(thread_id="tid", owner="o", repo="r", token="t")
    delete.assert_awaited_once()
    assert delete.await_args is not None
    assert delete.await_args.kwargs["comment_id"] == 99
    set_meta.assert_awaited_once_with("tid", extra={"status_comment_id": None})


@pytest.mark.asyncio
async def test_clear_review_started_comment_noop_without_tracked_id() -> None:
    delete = AsyncMock()
    set_meta = AsyncMock()
    with (
        patch("agent.review.publish.get_thread_metadata", AsyncMock(return_value={})),
        patch("agent.review.publish.delete_status_comment", delete),
        patch("agent.review.publish.set_reviewer_thread_metadata", set_meta),
    ):
        await clear_review_started_comment(thread_id="tid", owner="o", repo="r", token="t")
    delete.assert_not_called()
    set_meta.assert_not_called()


def test_render_review_body_with_only_out_of_diff_findings() -> None:
    body = render_review_body(
        pr_number=7,
        surfaced_count=0,
        out_of_diff_findings=[
            _f(title="Caller passes stale arg", description="boom", file="x/caller.py")
        ],
    )
    assert "No issues found" not in body
    assert "found no issues in the changed lines" in body
    assert "<details>" in body
    assert "1 out-of-diff finding</summary>" in body
    assert "**Caller passes stale arg**" in body
    assert "`x/caller.py" in body


def test_render_review_body_combines_inline_and_out_of_diff() -> None:
    body = render_review_body(
        pr_number=7,
        surfaced_count=2,
        out_of_diff_findings=[_f(title="A"), _f(title="B")],
    )
    assert "found 2 potential issues." in body
    assert "2 out-of-diff findings</summary>" in body
    assert "<!-- open-swe-reviewer pr=7 -->" in body


def test_render_review_body_includes_trace_link_when_provided() -> None:
    body = render_review_body(
        pr_number=123,
        surfaced_count=0,
        trace_url="https://smith.langchain.com/o/t/project/p/t/thread-id",
    )
    assert "[View Open SWE trace](https://smith.langchain.com/o/t/project/p/t/thread-id)" in body
    assert body.endswith("<!-- open-swe-reviewer pr=123 -->")


async def test_publish_review_eval_mode_does_not_call_github() -> None:
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
        result = await publish_review()

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["surfaced_count"] == 1
    assert result["hidden_count"] == 1
    get_token.assert_not_called()
    post_review.assert_not_called()
    set_meta.assert_awaited_once_with(
        "tid",
        last_reviewed_sha="sha",
        extra={
            "reviewer_eval_publication": {
                "finding_ids": ["f_high"],
                "severity_threshold": "medium",
                "cap": 6,
            }
        },
    )


async def test_publish_review_eval_mode_uses_configured_cap() -> None:
    from agent.tools.publish_review import publish_review

    findings = [
        _f(id="f_first", severity="high", file="a.py", start_line=1, end_line=1),
        _f(id="f_second", severity="high", file="b.py", start_line=2, end_line=2),
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
                    "reviewer_eval_cap": 1,
                },
                "metadata": {},
            },
        ),
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()) as set_meta,
    ):
        result = await publish_review()

    assert result["surfaced_count"] == 1
    assert set_meta.await_args is not None
    publication = set_meta.await_args.kwargs["extra"]["reviewer_eval_publication"]
    assert publication["cap"] == 1
    assert publication["finding_ids"] == ["f_first"]


@pytest.mark.asyncio
async def test_publish_review_surfaces_additional_findings_count_in_body() -> None:
    """When all surfaced findings are above threshold but sub-threshold findings
    exist, the review body must mention how many additional findings are in the
    web app."""
    from agent.tools.publish_review import _publish_review_async

    findings = [
        _f(id="f_low_1", severity="low", file="a.py", start_line=1, end_line=1),
        _f(id="f_low_2", severity="low", file="b.py", start_line=2, end_line=2),
    ]
    post_review = AsyncMock(return_value={"id": 555})
    fetch_comments = AsyncMock(return_value=[])

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
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()),
        patch("agent.tools.publish_review._maybe_post_slack_completion_reply", AsyncMock()),
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
    assert post_review.await_args is not None
    posted_body = post_review.await_args.kwargs["body"]
    assert "No issues found" in posted_body
    assert "2 additional findings can be viewed in the web app." in posted_body


async def test_publish_review_forwards_trace_link_config_override() -> None:
    from agent.tools.publish_review import publish_review

    publish_async = AsyncMock(return_value={"success": True})
    with (
        patch(
            "agent.tools.publish_review.get_config",
            return_value={
                "configurable": {
                    "thread_id": "reviewer-thread-id",
                    "repo": {"owner": "o", "name": "r"},
                    "pr_number": 7,
                    "head_sha": "sha",
                    "review_trace_link_enabled": False,
                },
                "metadata": {},
            },
        ),
        patch("agent.tools.publish_review.get_github_token", return_value="token"),
        patch("agent.tools.publish_review._publish_review_async", publish_async),
    ):
        result = await publish_review()

    assert result == {"success": True}
    assert publish_async.call_args is not None
    assert publish_async.call_args.kwargs["trace_link_config_override"] is False


@pytest.mark.asyncio
async def test_resolve_review_trace_url_enabled_by_team_setting() -> None:
    from agent.tools.publish_review import _resolve_review_trace_url

    with (
        patch(
            "agent.tools.publish_review.get_team_review_trace_links_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review.get_langsmith_trace_url",
            return_value="https://smith/t",
        ),
    ):
        url = await _resolve_review_trace_url("reviewer-thread-id", None)

    assert url == "https://smith/t"


@pytest.mark.asyncio
async def test_resolve_review_trace_url_disabled_by_team_setting() -> None:
    from agent.tools.publish_review import _resolve_review_trace_url

    trace_url = MagicMock(return_value="https://smith/t")
    with (
        patch(
            "agent.tools.publish_review.get_team_review_trace_links_enabled",
            AsyncMock(return_value=False),
        ),
        patch("agent.tools.publish_review.get_langsmith_trace_url", trace_url),
    ):
        url = await _resolve_review_trace_url("reviewer-thread-id", None)

    assert url is None
    trace_url.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_review_trace_url_config_override_skips_team_lookup() -> None:
    from agent.tools.publish_review import _resolve_review_trace_url

    team_lookup = AsyncMock(return_value=True)
    trace_url = MagicMock(return_value="https://smith/t")
    with (
        patch("agent.tools.publish_review.get_team_review_trace_links_enabled", team_lookup),
        patch("agent.tools.publish_review.get_langsmith_trace_url", trace_url),
    ):
        url = await _resolve_review_trace_url("reviewer-thread-id", False)

    assert url is None
    team_lookup.assert_not_called()
    trace_url.assert_not_called()


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

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
        ok = await resolve_review_thread(thread_node_id="T_1", token="t")
    assert ok is True


@pytest.mark.asyncio
async def test_fetch_pr_review_threads_handles_null_repository() -> None:
    """GitHub returns ``repository: null`` when the token can't read the repo
    (SAML, expired token, private/deleted). ``dict.get(k, {})`` does not coalesce
    explicit null, so the fetch must guard against it and return collected threads."""
    response = MagicMock()
    response.json.return_value = {"data": {"repository": None}}
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=response)

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
        threads = await fetch_pr_review_threads(owner="o", repo="r", pr_number=1, token="t")
    assert threads == []


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

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
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

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
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
    assert post_review.await_args is not None
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


@pytest.mark.parametrize(
    ("status", "flag", "expected"),
    [
        (
            "open",
            "true",
            (
                "failure",
                "Found 1 potential issue",
                "Open SWE surfaced 1 potential issue on this pull request.",
            ),
        ),
        (
            "resolved",
            "true",
            (
                "success",
                "No issues found",
                "Open SWE reviewed this pull request and found no issues.",
            ),
        ),
        (
            "open",
            None,
            (
                "success",
                "No issues found",
                "Open SWE reviewed this pull request and found no issues.",
            ),
        ),
    ],
    ids=[
        "standing-open-surfaced-blocking-fails",
        "standing-resolved-blocking-succeeds",
        "standing-open-surfaced-informational-unchanged",
    ],
)
async def test_publish_review_check_counts_standing_findings(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    flag: str | None,
    expected: tuple[str, str, str],
) -> None:
    from agent.tools.publish_review import _publish_review_async

    if flag is None:
        monkeypatch.delenv("REVIEW_CHECK_BLOCKING", raising=False)
    else:
        monkeypatch.setenv("REVIEW_CHECK_BLOCKING", flag)

    finding = _f(
        id="f_standing",
        status=status,
        github_review_comment_id=100,
    )
    surface = finding["surface"]
    assert surface is not None
    surface["state"] = "surfaced"
    settle = AsyncMock()
    post_review = AsyncMock()

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch(
            "agent.tools.publish_review.list_findings_async",
            AsyncMock(return_value=[finding]),
        ),
        patch(
            "agent.tools.publish_review._open_swe_already_reviewed",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            AsyncMock(return_value=0),
        ),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", AsyncMock()),
        patch("agent.tools.publish_review.settle_review_check_run", settle),
        patch(
            "agent.tools.publish_review._resolve_review_trace_url",
            AsyncMock(return_value=None),
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
    settle.assert_awaited_once()
    call = settle.await_args
    assert call is not None
    assert (
        call.kwargs["conclusion"],
        call.kwargs["title"],
        call.kwargs["summary"],
    ) == expected
    assert result["surfaced_count"] == 0


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
            github_review_comment_id=None,
            github_review_id=None,
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
            cap=15,
            is_re_review=True,
        )

    post_review.assert_not_called()
    assert result["success"] is True
    assert result["review_id"] is None
    assert result["surfaced_count"] == 0
    assert "out_of_diff_count" not in result
    assert result["skipped_empty_re_review"] is True


@pytest.mark.asyncio
async def test_publish_review_skips_duplicate_empty_summary_when_open_swe_already_reviewed() -> (
    None
):
    """A push landing mid-run is queued into the still-running first-review run,
    whose configurable still says re_review=False. With nothing to surface, the
    empty-review guard must key off the existing Open SWE review summary on the
    PR (not the stale flag) so it does not post a duplicate "No issues found"."""
    from agent.tools.publish_review import _publish_review_async

    post_review = AsyncMock()
    set_metadata = AsyncMock()
    resolve_threads = AsyncMock(return_value=0)
    review_exists = AsyncMock(return_value=True)
    slack_reply = AsyncMock()

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch("agent.tools.publish_review.open_swe_review_exists", review_exists),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review._resolve_threads_for_resolved_findings", resolve_threads),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", set_metadata),
        patch("agent.tools.publish_review._maybe_post_slack_completion_reply", slack_reply),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="newsha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
        )

    post_review.assert_not_called()
    review_exists.assert_awaited_once()
    resolve_threads.assert_awaited_once()
    slack_reply.assert_not_called()
    assert result["success"] is True
    assert result["review_id"] is None
    assert result["surfaced_count"] == 0
    assert result["skipped_empty_re_review"] is True
    set_metadata.assert_awaited_once_with("tid", last_reviewed_sha="newsha")


@pytest.mark.asyncio
async def test_publish_review_uses_resolved_head_sha_for_commit_and_last_reviewed() -> None:
    """A push that landed mid-run updates the live head in thread metadata.
    publish_review must anchor the GitHub review to that head and advance
    last_reviewed_sha to it, not the stale head frozen in the run config."""
    from agent.tools.publish_review import _publish_review_async

    finding = _f(id="f_new", file="b.py", start_line=2, end_line=2)
    post_review = AsyncMock(return_value={"id": 4242})
    set_metadata = AsyncMock()

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[finding])),
        patch(
            "agent.tools.publish_review.resolve_review_head_sha",
            AsyncMock(return_value="freshhead"),
        ),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
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
            head_sha="stalehead",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
            langgraph_run_id="run-x",
        )

    assert result["success"] is True
    assert post_review.await_args is not None
    assert post_review.await_args.kwargs["head_sha"] == "freshhead"
    final = set_metadata.await_args_list[-1]
    assert final.args[0] == "tid"
    assert final.kwargs["last_reviewed_sha"] == "freshhead"


@pytest.mark.asyncio
async def test_publish_review_skips_review_existence_check_on_re_review() -> None:
    """When re_review is already True we know a prior review exists, so the
    empty-review guard must short-circuit without an extra reviews API call."""
    from agent.tools.publish_review import _publish_review_async

    review_exists = AsyncMock(return_value=True)

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch("agent.tools.publish_review.open_swe_review_exists", review_exists),
        patch("agent.tools.publish_review.post_pull_request_review", AsyncMock()) as post_review,
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
            cap=15,
            is_re_review=True,
        )

    review_exists.assert_not_called()
    post_review.assert_not_called()
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
            cap=15,
            is_re_review=False,
        )

    review_exists.assert_not_called()
    post_review.assert_not_called()
    assert result["skipped_empty_re_review"] is True


@pytest.mark.asyncio
async def test_publish_review_posts_summary_when_review_existence_unknown() -> None:
    """When the reviews API can't answer (``open_swe_review_exists`` returns
    ``None``) and there is no durable prior-review signal, the guard must NOT
    suppress — re-posting the summary is the safe failure mode, never silently
    swallowing the only review the user sees."""
    from agent.tools.publish_review import _publish_review_async

    review_exists = AsyncMock(return_value=None)
    post_review = AsyncMock(return_value={"id": 321})

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review.get_thread_metadata",
            AsyncMock(return_value={}),
        ),
        patch("agent.tools.publish_review.open_swe_review_exists", review_exists),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.fetch_review_comments", AsyncMock(return_value=[])),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("agent.tools.publish_review.set_reviewer_thread_metadata", new_callable=AsyncMock),
        patch("agent.tools.publish_review._maybe_post_slack_completion_reply", AsyncMock()),
    ):
        result = await _publish_review_async(
            owner="o",
            repo="r",
            pr_number=7,
            head_sha="newsha",
            token="t",
            severity_threshold="medium",
            cap=15,
            is_re_review=False,
        )

    review_exists.assert_awaited_once()
    post_review.assert_awaited_once()
    assert "skipped_empty_re_review" not in result
    assert result["review_id"] == 321


@pytest.mark.asyncio
async def test_open_swe_review_exists_detects_summary_marker() -> None:
    response = MagicMock()
    response.json.return_value = [
        {"id": 1, "body": "some human review"},
        {"id": 2, "body": f"## ✅ Open SWE Review\n\n{review_summary_marker(7)}"},
    ]
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.get = AsyncMock(return_value=response)

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
        exists = await open_swe_review_exists(owner="o", repo="r", pr_number=7, token="t")
    assert exists is True


@pytest.mark.asyncio
async def test_open_swe_review_exists_false_without_marker() -> None:
    response = MagicMock()
    response.json.return_value = [{"id": 1, "body": "looks good to me"}]
    response.raise_for_status.return_value = None

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.get = AsyncMock(return_value=response)

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
        exists = await open_swe_review_exists(owner="o", repo="r", pr_number=7, token="t")
    assert exists is False


@pytest.mark.asyncio
async def test_open_swe_review_exists_returns_none_on_http_error() -> None:
    """A failed reviews API call is reported as ``None`` (unknown), never
    ``False`` — the empty-summary dedup must not treat a transient failure as
    "no prior review exists" and double-post."""
    import httpx

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.get = AsyncMock(side_effect=httpx.HTTPError("boom"))

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
        exists = await open_swe_review_exists(owner="o", repo="r", pr_number=7, token="t")
    assert exists is None


@pytest.mark.asyncio
async def test_re_review_backfills_existing_marker_and_skips_duplicate_post() -> None:
    from agent.tools.publish_review import _publish_review_async

    finding = _f(id="f_old", first_seen_sha="oldsha", github_review_comment_id=None)
    findings = [finding]
    thread = {
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
    }
    post_review = AsyncMock()

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch(
            "agent.tools.publish_review.fetch_pr_review_threads", AsyncMock(return_value=[thread])
        ),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.review.reconcile.list_findings", AsyncMock(return_value=findings)),
        patch("agent.review.reconcile.replace_findings", AsyncMock()),
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
            cap=15,
            is_re_review=True,
        )

    post_review.assert_not_called()
    assert result["skipped_empty_re_review"] is True
    assert findings[0]["github_review_comment_id"] == 101
    assert findings[0]["github_review_thread_id"] == "THREAD_1"


@pytest.mark.asyncio
async def test_re_review_backfills_and_resolves_duplicate_existing_threads() -> None:
    from agent.tools.publish_review import _publish_review_async

    finding = _f(
        id="f_old",
        first_seen_sha="oldsha",
        github_review_comment_id=None,
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
            cap=15,
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
    assert findings[0]["github_thread_resolved"] is True


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
            cap=15,
            is_re_review=False,
        )

    assert result["success"] is True
    assert result["review_id"] == 999
    assert fetch_threads.await_count == 2
    assert findings[0]["github_review_id"] == 999
    assert findings[0]["github_review_comment_id"] == 202
    assert findings[0]["github_review_thread_id"] == "THREAD_1"


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
            cap=15,
            is_re_review=True,
        )

    assert result["success"] is True
    assert result["surfaced_count"] == 1
    assert post_review.await_args is not None
    inline_comments = post_review.await_args.kwargs["inline_comments"]
    assert [comment["path"] for comment in inline_comments] == ["new.py"]
    assert old["github_review_id"] is None
    assert new["github_review_id"] == 888
    assert new["github_review_comment_id"] == 303


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
            cap=15,
            is_re_review=False,
        )

    assert result["success"] is True
    by_id = {f["id"]: f for f in findings}
    assert by_id["f_one"]["github_review_comment_id"] == 901
    assert by_id["f_two"]["github_review_comment_id"] == 902


@pytest.mark.asyncio
async def test_publish_review_records_review_id_and_comment_id_in_single_write() -> None:
    """The post-publish bookkeeping stamps review id + comment id onto findings
    in one ``replace_findings`` call, so a finding is never persisted with a
    review id but no comment id."""
    from agent.tools.publish_review import _publish_review_async

    finding = _f(id="f_new", file="x.py", start_line=3, end_line=3)
    findings = [finding]
    post_review = AsyncMock(return_value={"id": 555})
    fetch_comments = AsyncMock(
        return_value=[
            {"id": 808, "path": "x.py", "line": 3, "body": render_inline_comment_body(finding)},
        ]
    )
    replace = AsyncMock()

    with (
        patch("agent.tools.publish_review.get_thread_id_from_runtime", return_value="tid"),
        patch("agent.tools.publish_review.list_findings_async", AsyncMock(return_value=findings)),
        patch("agent.tools.publish_review.replace_findings", replace),
        patch("agent.tools.publish_review.post_pull_request_review", post_review),
        patch("agent.tools.publish_review.fetch_review_comments", fetch_comments),
        patch("agent.tools.publish_review._store_thread_ids_on_findings", new_callable=AsyncMock),
        patch(
            "agent.tools.publish_review._resolve_threads_for_resolved_findings",
            new_callable=AsyncMock,
            return_value=0,
        ),
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
            cap=15,
            is_re_review=False,
        )

    assert result["success"] is True
    # Exactly one persisted snapshot carries both ids together — never a
    # half-stamped intermediate state.
    persisted_snapshots = [call.args[1] for call in replace.await_args_list]
    assert any(
        snap[0].get("github_review_id") == 555 and snap[0].get("github_review_comment_id") == 808
        for snap in persisted_snapshots
    )
    assert all(
        not (
            snap[0].get("github_review_id") == 555
            and snap[0].get("github_review_comment_id") is None
        )
        for snap in persisted_snapshots
    )


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
    assert post_review.await_args is not None
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
    assert slack_post.await_args is not None
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
    assert slack_post.await_args is not None
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

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
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

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
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

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
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

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
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

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
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

    with patch("agent.utils.github_http.httpx.AsyncClient", return_value=client_cm):
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
        patch("agent.tools.publish_review.get_github_token", return_value="token"),
        patch("agent.tools.publish_review._publish_review_async", publish_async),
    ):
        result = await publish_review()

    assert result["success"] is False
    assert result["error"] == "thread_not_found"
    assert result["thread_id"] == "tid"
    assert "Do not retry" in result["note"]
