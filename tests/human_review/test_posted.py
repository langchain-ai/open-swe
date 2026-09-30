from unittest.mock import AsyncMock, patch

import pytest

from agent.expedited_review.readiness import PullRequestSnapshot
from agent.github.pull_requests import PullRequest
from agent.github.repo_files import RepoSettings
from agent.human_review.posted import linked_pull_request, watch_post
from agent.human_review.requests import HumanReviewRequest
from agent.users import User


def test_a_slack_link_and_its_bare_repeat_are_one_pull_request() -> None:
    text = (
        "<https://github.com/lc/repo/pull/7|lc/repo#7> please review "
        "https://github.com/LC/repo/pull/7/files"
    )
    ref = linked_pull_request(text)
    assert ref is not None
    assert (ref.owner.lower(), ref.repo, ref.number) == ("lc", "repo", 7)


def test_a_message_linking_several_pull_requests_is_not_watched() -> None:
    text = "<https://github.com/lc/repo/pull/7> and <https://github.com/lc/repo/pull/8>"
    assert linked_pull_request(text) is None


@pytest.mark.parametrize("review_channel", ["", "C_OTHER_REVIEW_CHANNEL"])
async def test_watch_uses_the_posted_channel_not_repository_settings(review_channel: str) -> None:
    user = User(preferences={"review_channel_watch": True})
    pull_request = PullRequest(owner="lc", repo="repo", number=7)
    snapshot = PullRequestSnapshot(
        state="open",
        merged=False,
        draft=False,
        head_sha="abc",
        title="Fix",
        author="ada",
        mergeable=True,
        mergeable_state="clean",
        check_state="success",
        unresolved_threads=0,
    )
    saved: list[HumanReviewRequest] = []

    async def save(request: HumanReviewRequest) -> HumanReviewRequest:
        saved.append(request)
        return request

    with (
        patch("agent.human_review.posted.User.for_identity", AsyncMock(return_value=user)),
        patch("agent.human_review.posted.repo_token", AsyncMock(return_value="token")),
        patch.object(
            RepoSettings,
            "cached",
            AsyncMock(return_value=RepoSettings(review_channel=review_channel)),
        ),
        patch.object(HumanReviewRequest, "active_for", AsyncMock(return_value=None)),
        patch(
            "agent.human_review.posted.record_pull_request",
            AsyncMock(return_value=(pull_request, snapshot)),
        ),
        patch.object(HumanReviewRequest, "save", save),
        patch("agent.human_review.posted.settle", AsyncMock()),
    ):
        await watch_post("C_TEAM", "123.456", "U_ADA", "https://github.com/lc/repo/pull/7")

    assert len(saved) == 1
    request = saved[0]
    assert request.kind == "posted"
    assert request.pull_request_id == pull_request.id
    assert request.head_sha == "abc"
    assert request.requested_by_user_id == user.id
    assert (request.slack_channel_id, request.slack_message_ts) == ("C_TEAM", "123.456")
