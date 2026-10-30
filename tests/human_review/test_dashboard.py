"""Dashboard review selection without an agent run or pre-existing PR record."""

from unittest.mock import AsyncMock, patch

import pytest

from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review.dashboard import DashboardReviewRequest
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import Origin, RequestResult
from openswe.slack.channels import SlackChannel
from openswe.slack.client import GitHubPrRef
from openswe.users import User


@pytest.mark.parametrize(
    "case", ["eligible", "large", "unregistered", "disabled", "draft", "active"]
)
async def test_dashboard_selects_review_workflow(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pr_ref = GitHubPrRef(owner="lc", repo="repo", number=7, url="https://github.com/lc/repo/pull/7")
    requester = User()
    pr = PullRequest(owner="lc", repo="repo", number=7)
    approval = HumanReviewRequest(
        pull_request_id=pr.id, head_sha="abc", kind="expedited", slack_channel_id="C1"
    )
    target = SlackChannel(id="C1", name="reviews")
    github = GitHubClient.connect(token="token")
    app_github = GitHubClient.connect(token="token")
    for method in ("link_on_github", "broadcast_configured", "remove_superseded"):
        monkeypatch.setattr(ReviewCard, method, AsyncMock())
    monkeypatch.setattr(ReviewCard, "prompt_author_ready", AsyncMock(return_value=""))
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
            AsyncMock(return_value=approval if case == "active" else None),
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
                return_value={
                    "state": "open",
                    "head": {"sha": "abc"},
                    "base": {"ref": "main"},
                    "user": {"login": "alice"},
                    "title": "Manual PR",
                }
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
            "openswe.expedited_review.nomination.latest_review_states", AsyncMock(return_value={})
        ),
        patch.object(
            User, "for_login", AsyncMock(return_value=None if case == "unregistered" else User())
        ),
        patch.object(RepoSettings, "cached", AsyncMock(return_value=RepoSettings())),
        patch.object(PullRequest, "load", AsyncMock(return_value=pr)),
        patch.object(PullRequest, "save", AsyncMock(return_value=pr)),
        patch.object(
            HumanReviewRequest, "save", autospec=True, side_effect=lambda row: row
        ) as save,
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=approval)),
        patch.object(ReviewCard, "post_expedited", AsyncMock(return_value="1.0")) as post,
        patch(
            "openswe.human_review.dashboard.get_slack_permalink",
            AsyncMock(return_value="https://slack/card"),
        ),
    ):
        result = await DashboardReviewRequest(pr_ref, Origin(requester=requester), "token").request(
            channel="C1"
        )
    assert result.success
    if case == "eligible":
        assert result.channel == "C1"
        assert result.permalink == "https://slack/card"
        post.assert_awaited_once()
        standard.assert_not_awaited()
        saved = save.call_args_list[0].args[0]
        assert saved.kind == "expedited"
        assert saved.requested_by_user_id == requester.id
        assert saved.thread_id == ""
        assert saved.run_config == {}
    else:
        assert result.request_id == "standard"
        post.assert_not_awaited()
        standard.assert_awaited_once()
