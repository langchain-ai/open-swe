"""Dashboard review selection without an agent run or pre-existing PR record."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.expedited_review.eligibility import ChangedFile, diff_fingerprint
from openswe.expedited_review.nomination import ExpeditedReviewNomination
from openswe.github.codeowners import CodeOwners
from openswe.github.http import GitHubClient, RepoFileUnreadableError
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review import routes
from openswe.human_review.dashboard import DashboardReviewRequest
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import Origin, RequestResult
from openswe.review.assessment_feedback import PublishedAssessment
from openswe.slack.channels import ChannelLookupUnavailable, SlackChannel
from openswe.slack.client import GitHubPrRef
from openswe.slack.http import SlackRequestError
from openswe.users import User
from openswe.users.models import UserIdentity


@pytest.mark.parametrize("expedited", [True, False])
@pytest.mark.parametrize("unavailable", [True, False])
async def test_review_channel_lookup_failure_is_actionable(
    monkeypatch: pytest.MonkeyPatch, expedited: bool, unavailable: bool
) -> None:
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=User()))
    monkeypatch.setattr(routes, "require_repo_access_for_user", AsyncMock(return_value="token"))
    monkeypatch.setattr(HumanReviewRequest, "active_for", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "openswe.human_review.dashboard.get_workspace_settings",
        AsyncMock(return_value=WorkspaceSettings({"expedited_review_enabled": expedited})),
    )
    monkeypatch.setattr(
        "openswe.human_review.dashboard.review_blockers", AsyncMock(return_value=[])
    )
    monkeypatch.setattr("openswe.human_review.standard.review_blockers", AsyncMock(return_value=[]))
    monkeypatch.setattr("openswe.human_review.standard.EventLog.append", AsyncMock())
    monkeypatch.setattr(
        SlackChannel,
        "resolve",
        AsyncMock(side_effect=ChannelLookupUnavailable(30))
        if unavailable
        else AsyncMock(return_value=None),
    )
    with (
        patch.object(GitHubClient, "as_app", return_value=GitHubClient.connect(token="token")),
        pytest.raises(HTTPException) as raised,
    ):
        await routes.api_request_human_review(
            "lc", "repo", 7, routes.HumanReviewRequestBody(channel="reviews"), {"sub": "alice"}
        )

    assert raised.value.status_code == (503 if unavailable else 409)
    if unavailable:
        assert raised.value.headers == {"Retry-After": "30"}
        assert "30 seconds" in raised.value.detail
        assert "not found" not in raised.value.detail
    else:
        assert "'reviews'" in raised.value.detail
        assert "make sure Robin has access" in raised.value.detail


@pytest.mark.parametrize(
    "case",
    [
        "eligible",
        "custom_summary",
        "large",
        "unregistered",
        "disabled",
        "draft",
        "active",
        "active_ready",
        "active_changed",
        "active_post_failed",
        "active_save_failed",
        "active_timestamp_failed",
        "active_cleanup_failed",
        "active_other_user",
        "active_pending_other_user",
        "active_unposted",
        "reviews_unavailable",
        "codeowners_unreadable",
        "already_reviewed",
    ],
)
async def test_dashboard_selects_review_workflow(
    case: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pr_ref = GitHubPrRef(owner="lc", repo="repo", number=7, url="https://github.com/lc/repo/pull/7")
    requester = User(identities=[UserIdentity(provider="slack", external_id="U1")])
    pr = PullRequest(owner="lc", repo="repo", number=7, author_user_id=requester.id)
    approval = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="expedited",
        slack_channel_id="C1",
        slack_message_ts="1.0",
    )
    if case == "active_pending_other_user":
        pr.author_user_id = User().id
    approval.requested_by_user_id = (
        User().id if case in {"active_other_user", "active_pending_other_user"} else requester.id
    )
    delete = AsyncMock(return_value=case != "active_cleanup_failed")
    monkeypatch.setattr("openswe.expedited_review.nomination.delete_slack_message", delete)
    discard = AsyncMock()
    reopen = AsyncMock()
    monkeypatch.setattr(ExpeditedReviewNomination, "discard", discard)
    monkeypatch.setattr(ReviewCard, "reopen", reopen)
    monkeypatch.setattr(PublishedAssessment, "latest", AsyncMock(return_value=None))
    approval.pull_request = pr
    approval.diff_fingerprint = diff_fingerprint(
        [
            ChangedFile(
                filename="app.py",
                additions=1,
                patch="+old"
                if case
                in {
                    "active_changed",
                    "active_post_failed",
                    "active_save_failed",
                    "active_timestamp_failed",
                    "active_cleanup_failed",
                }
                else "+x",
            )
        ]
    )
    approval.awaiting_ready = case in {"active_ready", "active_pending_other_user"}
    if approval.awaiting_ready or case == "active_unposted":
        approval.slack_message_ts = ""
    if approval.awaiting_ready:
        approval.slack_dm_channel_id = "D1"
        approval.slack_dm_message_ts = "4.0"
    monkeypatch.setattr(ReviewCard, "refresh", AsyncMock())
    monkeypatch.setattr(ReviewCard, "retire", AsyncMock(return_value=approval))
    monkeypatch.setattr(
        "openswe.human_review.requests.get_slack_permalink",
        AsyncMock(return_value="https://slack/dm"),
    )
    monkeypatch.setattr(SlackChannel, "name_for", AsyncMock(return_value="reviews"))
    monkeypatch.setattr(HumanReviewRequest, "log_event", AsyncMock())

    async def save_request(row: HumanReviewRequest) -> HumanReviewRequest:
        if case == "active_save_failed" or (
            case in {"active_timestamp_failed", "active_cleanup_failed"} and row.slack_message_ts
        ):
            raise RuntimeError("save failed")
        return row

    target = SlackChannel(id="C1", name="reviews")
    github = GitHubClient.connect(token="token")
    app_github = GitHubClient.connect(token="token")
    for method in ("broadcast_configured", "remove_superseded"):
        monkeypatch.setattr(ReviewCard, method, AsyncMock())
    monkeypatch.setattr(ReviewCard, "prompt_author_ready", AsyncMock(return_value=""))
    monkeypatch.setattr(
        CodeOwners,
        "fetch",
        AsyncMock(side_effect=RepoFileUnreadableError("unreadable"))
        if case == "codeowners_unreadable"
        else AsyncMock(return_value=None),
    )
    with (
        patch(
            "openswe.human_review.dashboard.get_workspace_settings",
            AsyncMock(
                return_value=WorkspaceSettings({"expedited_review_enabled": case != "disabled"})
            ),
        ),
        patch.object(
            HumanReviewRequest,
            "active_for",
            AsyncMock(return_value=approval if case.startswith("active") else None),
        ),
        patch.object(GitHubClient, "connect", return_value=github),
        patch.object(GitHubClient, "as_app", return_value=app_github),
        patch(
            "openswe.human_review.dashboard.review_blockers",
            AsyncMock(return_value=["it is a draft"] if case == "draft" else []),
        ),
        patch("openswe.human_review.dashboard.target_channel", AsyncMock(return_value=target)),
        patch(
            "openswe.human_review.dashboard.request_review",
            AsyncMock(return_value=RequestResult(success=True, request_id="standard")),
        ) as standard,
        patch.object(
            PullRequestClient,
            "pull",
            AsyncMock(
                side_effect=AssertionError("Retrieval must not nominate or read GitHub")
                if case in {"active_other_user", "active_pending_other_user"}
                else None,
                return_value={
                    "state": "open",
                    "head": {"sha": "abc"},
                    "base": {"ref": "main"},
                    "user": {"login": "alice"},
                    "title": "Manual PR",
                    "body": "<!-- hidden -->\nThe PR description.\nWith more context.",
                },
            ),
        ),
        patch.object(
            ChangedFile,
            "of_pull",
            AsyncMock(
                return_value=[
                    ChangedFile(
                        filename="app.py", additions=30 if case == "large" else 1, patch="+x"
                    )
                ]
            ),
        ),
        patch(
            "openswe.expedited_review.nomination.latest_review_states",
            AsyncMock(
                return_value=None
                if case == "reviews_unavailable"
                else {"bob": "APPROVED"}
                if case in {"codeowners_unreadable", "already_reviewed"}
                else {}
            ),
        ),
        patch.object(
            User, "for_login", AsyncMock(return_value=None if case == "unregistered" else User())
        ),
        patch.object(RepoSettings, "cached", AsyncMock(return_value=RepoSettings())),
        patch.object(PullRequest, "load", AsyncMock(return_value=pr)),
        patch.object(PullRequest, "save", AsyncMock(return_value=pr)),
        patch.object(
            HumanReviewRequest,
            "save",
            autospec=True,
            side_effect=save_request,
        ) as save,
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=approval)),
        patch.object(
            ReviewCard,
            "post_expedited",
            AsyncMock(
                return_value="1.0",
                side_effect=SlackRequestError("post_failed")
                if case == "active_post_failed"
                else None,
            ),
        ) as post,
        patch(
            "openswe.slack.client.get_slack_permalink", AsyncMock(return_value="https://slack/card")
        ),
        patch("openswe.slack.dm.post_dm", AsyncMock()) as dm,
    ):
        if case in {"active_save_failed", "active_timestamp_failed", "active_cleanup_failed"}:
            with pytest.raises(RuntimeError, match="save failed"):
                await DashboardReviewRequest(pr_ref, Origin(requester=requester), "token").request()
            discard.assert_awaited_once()
            reopen.assert_awaited_once()
            if case == "active_save_failed":
                delete.assert_not_awaited()
            else:
                delete.assert_awaited_once_with("C1", "1.0")
            if case == "active_cleanup_failed":
                assert "Failed to remove orphaned expedited review card" in caplog.text
            return
        result = await DashboardReviewRequest(pr_ref, Origin(requester=requester), "token").request(
            channel="C1", inline_summary="Custom summary." if case == "custom_summary" else None
        )
    if case == "active_post_failed":
        assert not result.success
        assert "post_failed" in result.error
        discard.assert_awaited_once()
        reopen.assert_awaited_once()
        standard.assert_not_awaited()
        return
    if case == "active_unposted":
        assert not result.success
        assert "not posted yet" in result.error
        standard.assert_not_awaited()
        return
    assert result.success
    dm.assert_not_awaited()
    if case in {"eligible", "custom_summary", "active_changed"}:
        assert result.channel == "C1"
        assert result.permalink == "https://slack/card"
        post.assert_awaited_once()
        standard.assert_not_awaited()
        saved = save.call_args_list[0].args[0]
        assert saved.tldr == (
            "Custom summary."
            if case == "custom_summary"
            else "The PR description. With more context."
        )
        assert saved.kind == "expedited"
        assert saved.requested_by_user_id == requester.id
        assert saved.thread_id == ""
        assert saved.run_config == {"expedited_review_channel": ""}
    elif case in {"active", "active_ready", "active_other_user", "active_pending_other_user"}:
        assert result.request_id == str(approval.id)
        assert result.reused
        assert result.channel == "C1"
        assert result.permalink == (
            "https://slack/dm"
            if case == "active_ready"
            else ""
            if case == "active_pending_other_user"
            else "https://slack/card"
        )
        if case in {"active_ready", "active_pending_other_user"}:
            assert approval.awaiting_ready
            assert not approval.slack_message_ts
        post.assert_not_awaited()
        standard.assert_not_awaited()
    else:
        assert result.request_id == "standard"
        post.assert_not_awaited()
        standard.assert_awaited_once()
