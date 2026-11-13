from unittest.mock import AsyncMock, patch

import pytest
from pytest import MonkeyPatch

from openswe.github.http import RepoClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review import routes
from openswe.human_review.requests import HumanReviewRequest


def test_review_channel_most_files_and_ties(monkeypatch: MonkeyPatch) -> None:
    settings = RepoSettings.model_validate(
        {
            "reviewChannel": "#fallback",
            "reviewChannelRules": [
                {"paths": ["ui/*"], "channel": "#frontend"},
                {"paths": ["agent/*"], "channel": "#backend"},
            ],
        }
    )
    assert settings.channel_for_files(["ui/a", "ui/nested/b", "agent/c"]) == "#frontend"
    choices: list[str] = []

    def choose(channels: list[str]) -> str:
        choices.extend(channels)
        return channels[-1]

    monkeypatch.setattr("openswe.github.repo_files.random.choice", choose)
    assert settings.channel_for_files(["ui/a", "agent/b", "README.md"]) == "#backend"
    assert set(choices) == {"#frontend", "#backend"}
    choices.clear()
    assert settings.channel_for_files(["ui/a", "ui/b", "agent/c", "README.md"]) == "#frontend"
    assert choices == ["#frontend"]
    assert settings.channel_for_files(["README.md"]) == ""
    assert RepoSettings(reviewChannel="#fallback").channel_for_files(["README.md"]) == "#fallback"


@pytest.mark.parametrize("active_only", [False, True])
@pytest.mark.parametrize("routed", [False, True])
@pytest.mark.parametrize("filename", ["ui/button.tsx", "README.md"])
async def test_review_availability_uses_cached_settings(
    active_only: bool, routed: bool, filename: str, github_app: AsyncMock
) -> None:
    settings = RepoSettings.model_validate(
        {
            "reviewChannel": "#fallback",
            "reviewChannelRules": ([{"paths": ["ui/*"], "channel": "#frontend"}] if routed else []),
        }
    )
    with (
        patch.object(routes, "require_repo_access_for_user", AsyncMock()),
        patch.object(RepoSettings, "cached", AsyncMock(return_value=settings)),
        patch.object(
            RepoClient, "read_file", AsyncMock(side_effect=AssertionError("uncached read"))
        ),
        patch.object(PullRequestClient, "head_sha", AsyncMock(return_value="abc")) as head,
        patch.object(
            PullRequestClient, "files", AsyncMock(return_value=[{"filename": filename}])
        ) as files,
        patch.object(routes.HumanReviewRequest, "active_for", AsyncMock(return_value=None)),
        patch.object(routes, "review_blockers", AsyncMock(return_value=[])) as blockers,
    ):
        result = await routes.api_human_review_availability(
            "lc", "repo", 7, session={"sub": "ada"}, active_only=active_only
        )
    if active_only:
        head.assert_not_awaited()
        files.assert_not_awaited()
        blockers.assert_not_awaited()
    assert result.available is (not active_only and (not routed or filename.startswith("ui/")))
    assert result.active is False


@pytest.mark.parametrize("active_only", [False, True])
async def test_active_review_links_to_slack_when_permalink_lookup_fails(
    active_only: bool, github_app: AsyncMock
) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    active = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="expedited",
        slack_channel_id="C1",
        slack_message_ts="123.456",
    )
    with (
        patch.object(routes, "require_repo_access_for_user", AsyncMock()),
        patch.object(RepoSettings, "cached", AsyncMock(return_value=RepoSettings())),
        patch.object(PullRequestClient, "head_sha", AsyncMock(return_value="abc")),
        patch.object(HumanReviewRequest, "active_for", AsyncMock(return_value=active)),
        patch.object(routes.User, "for_login", AsyncMock(return_value=None)),
        patch("openswe.slack.client.get_slack_permalink", AsyncMock(return_value=None)),
    ):
        result = await routes.api_human_review_availability(
            "lc", "repo", 7, session={"sub": "ada"}, active_only=active_only
        )
    assert result.active
    assert result.permalink.endswith("/dashboard/api/slack/open?channel=C1&message_ts=123.456")
