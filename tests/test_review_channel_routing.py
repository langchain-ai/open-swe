import asyncio
from datetime import timedelta
from time import time_ns
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from fastapi import HTTPException
from pytest import MonkeyPatch

from openswe.github.http import GitHubClient, RepoClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review import routes, standard
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
        patch.object(
            PullRequestClient,
            "pull",
            AsyncMock(
                return_value={"state": "open", "head": {"sha": "abc"}, "base": {"sha": "base"}}
            ),
        ) as pull,
        patch.object(
            PullRequestClient, "files", AsyncMock(return_value=[{"filename": filename}])
        ) as files,
        patch.object(routes.HumanReviewRequest, "active_for", AsyncMock(return_value=None)),
        patch.object(standard, "_review_author_allowed", AsyncMock(return_value=True)),
    ):
        result = await routes.api_human_review_availability(
            "lc", "repo", 7, session={"sub": "ada"}, active_only=active_only
        )
    # One read serves the head check, routing and blockers; uncached routing rechecks the head.
    assert pull.await_count == (0 if active_only else 2 if routed else 1)
    if active_only:
        files.assert_not_awaited()
    assert result.blockers == []
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
        patch.object(PullRequestClient, "pull", AsyncMock(return_value={"head": {"sha": "abc"}})),
        patch.object(HumanReviewRequest, "active_for", AsyncMock(return_value=active)),
        patch.object(routes.User, "for_login", AsyncMock(return_value=None)),
        patch("openswe.slack.client.get_slack_permalink", AsyncMock(return_value=None)),
    ):
        result = await routes.api_human_review_availability(
            "lc", "repo", 7, session={"sub": "ada"}, active_only=active_only
        )
    assert result.active
    assert result.permalink.endswith("/dashboard/api/slack/open?channel=C1&message_ts=123.456")


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("failure", ["invalid", "empty", "oversized", "timeout", "http"])
async def test_unreadable_settings_without_a_usable_cache_read_as_empty(
    expired: bool, failure: str, github_app: AsyncMock
) -> None:
    with patch.object(
        GitHubClient,
        "request",
        AsyncMock(return_value=httpx2.Response(200, text='{"reviewChannel":"#frontend"}')),
    ) as read:
        if expired:
            assert (await RepoSettings.cached("lc", "repo")).review_channel == "#frontend"
        if failure == "timeout":
            read.side_effect = httpx2.ReadTimeout("unavailable")
        elif failure == "http":
            read.side_effect = httpx2.HTTPStatusError(
                "unavailable",
                request=httpx2.Request("GET", "https://api.github.com"),
                response=httpx2.Response(503),
            )
        else:
            read.return_value = httpx2.Response(
                200, text={"invalid": "{", "empty": "", "oversized": "x" * 10_001}[failure]
            )
        with patch("time.time_ns", return_value=time_ns() + 25 * 60 * 60 * 10**9):
            assert await RepoSettings.cached("lc", "repo") == RepoSettings()
            read.side_effect = None
            read.return_value = httpx2.Response(200, text='{"reviewChannel":"#recovered"}')
            assert (await RepoSettings.cached("lc", "repo")).review_channel == "#recovered"


async def test_failed_settings_refresh_preserves_the_last_good_channel(
    github_app: AsyncMock,
) -> None:
    finished = asyncio.Event()

    async def unavailable(*args: object, **kwargs: object) -> httpx2.Response:
        finished.set()
        raise httpx2.ReadTimeout("unavailable")

    with patch.object(
        GitHubClient,
        "request",
        AsyncMock(return_value=httpx2.Response(200, text='{"reviewChannel":"#frontend"}')),
    ) as read:
        assert (await RepoSettings.cached("lc", "repo")).review_channel == "#frontend"
        read.side_effect = unavailable
        with patch("openswe.github.repo_files.SETTINGS_FRESH_FOR", timedelta(0)):
            assert (await RepoSettings.cached("lc", "repo")).review_channel == "#frontend"
            await asyncio.wait_for(finished.wait(), timeout=1)
        assert (await RepoSettings.cached("lc", "repo")).review_channel == "#frontend"


async def test_channel_cache_tracks_diff_settings_and_pull_identity(github_app: AsyncMock) -> None:
    settings = RepoSettings.model_validate(
        {"reviewChannelRules": [{"paths": ["ui/*"], "channel": "#frontend"}]}
    )
    payload = {"head": {"sha": "head"}, "base": {"sha": "base"}}
    with (
        patch.object(PullRequestClient, "pull", AsyncMock(return_value=payload)) as read,
        patch.object(
            PullRequestClient, "files", AsyncMock(return_value=[{"filename": "ui/a"}])
        ) as files,
    ):
        async with GitHubClient.as_app("lc", "repo") as github:
            pull = github.repo("lc", "repo").pull_request(7)
            assert await settings.channel_for_pr(pull) == "#frontend"
            files.side_effect = AssertionError("unchanged diff must not fetch files")
            assert await settings.channel_for_pr(pull) == "#frontend"
            files.side_effect = None
            files.return_value = [{"filename": "README.md"}]
            payload["head"]["sha"] = "new-head"
            assert await settings.channel_for_pr(pull) == ""
            files.side_effect = AssertionError("an unrouted diff is also cached")
            assert await settings.channel_for_pr(pull) == ""
            files.side_effect = None
            files.return_value = [{"filename": "ui/a"}]
            payload["base"]["sha"] = "new-base"
            assert await settings.channel_for_pr(pull) == "#frontend"
            payload["base"]["sha"] = "base"
            assert await settings.channel_for_pr(pull) == ""
            read.side_effect = httpx2.ReadTimeout("unavailable")
            assert await settings.channel_for_pr(pull, for_display=True) == ""
            read.side_effect = None
            updated = RepoSettings.model_validate(
                {"reviewChannelRules": [{"paths": ["ui/*"], "channel": "#design"}]}
            )
            assert await updated.channel_for_pr(pull) == "#design"
            files.return_value = [{"filename": "README.md"}]
            assert await updated.channel_for_pr(pull.repo.pull_request(8)) == ""
            other = github.repo("lc", "other").pull_request(7)
            assert await updated.channel_for_pr(other) == ""


async def test_channel_cache_failure_does_not_block_live_routing(github_app: AsyncMock) -> None:
    settings = RepoSettings.model_validate(
        {"reviewChannelRules": [{"paths": ["ui/*"], "channel": "#frontend"}]}
    )
    with (
        patch("langgraph_api.cache.cache_get", AsyncMock(side_effect=RuntimeError("unavailable"))),
        patch("langgraph_api.cache.cache_set", AsyncMock(side_effect=RuntimeError("unavailable"))),
        patch.object(
            PullRequestClient,
            "pull",
            AsyncMock(return_value={"head": {"sha": "head"}, "base": {"sha": "base"}}),
        ),
        patch.object(PullRequestClient, "files", AsyncMock(return_value=[{"filename": "ui/a"}])),
    ):
        async with GitHubClient.as_app("lc", "repo") as github:
            assert (
                await settings.channel_for_pr(github.repo("lc", "repo").pull_request(7))
                == "#frontend"
            )


async def test_channel_cache_keeps_a_tied_route_stable(github_app: AsyncMock) -> None:
    settings = RepoSettings.model_validate(
        {
            "reviewChannelRules": [
                {"paths": ["ui/*"], "channel": "#frontend"},
                {"paths": ["agent/*"], "channel": "#backend"},
            ]
        }
    )
    with (
        patch.object(
            PullRequestClient,
            "pull",
            AsyncMock(return_value={"head": {"sha": "head"}, "base": {"sha": "base"}}),
        ),
        patch.object(
            PullRequestClient,
            "files",
            AsyncMock(return_value=[{"filename": "ui/a"}, {"filename": "agent/b"}]),
        ),
        patch("openswe.github.repo_files.random.choice", side_effect=["#frontend", "#backend"]),
    ):
        async with GitHubClient.as_app("lc", "repo") as github:
            pull = github.repo("lc", "repo").pull_request(7)
            assert await settings.channel_for_pr(pull) == "#frontend"
            assert await settings.channel_for_pr(pull) == "#frontend"


@pytest.mark.parametrize("failure", ["incomplete", "invalid", "raced", "unavailable"])
async def test_failed_routing_does_not_cache_a_channel(failure: str, github_app: AsyncMock) -> None:
    settings = RepoSettings.model_validate(
        {"reviewChannelRules": [{"paths": ["ui/*"], "channel": "#frontend"}]}
    )
    payload = {"head": {"sha": "head"}, "base": {"sha": "base"}, "changed_files": 1}
    with (
        patch.object(PullRequestClient, "pull", AsyncMock(return_value=payload)) as read,
        patch.object(
            PullRequestClient, "files", AsyncMock(return_value=[{"filename": "ui/a"}])
        ) as files,
    ):
        async with GitHubClient.as_app("lc", "repo") as github:
            pull = github.repo("lc", "repo").pull_request(7)
            assert await settings.channel_for_pr(pull) == "#frontend"
            payload["head"] = {"sha": "new-head"}
            if failure == "incomplete":
                files.return_value = []
            elif failure == "invalid":
                files.return_value = [{"filename": None}]
            elif failure == "raced":
                read.side_effect = [payload, {**payload, "base": {"sha": "new-base"}}]
            else:
                files.side_effect = httpx2.ReadTimeout("unavailable")
            with pytest.raises((ValueError, httpx2.ReadTimeout)):
                await settings.channel_for_pr(pull, for_display=True)
            read.side_effect = None
            files.side_effect = None
            files.return_value = [{"filename": "README.md"}]
            assert await settings.channel_for_pr(pull) == ""


@pytest.mark.parametrize(
    ("status", "limited"),
    [
        (None, False),
        (429, False),
        (503, False),
        (403, True),
        (401, False),
        (403, False),
        (404, False),
    ],
)
async def test_only_display_uses_last_channel_during_transient_failure(
    status: int | None, limited: bool, github_app: AsyncMock
) -> None:
    settings = RepoSettings.model_validate(
        {"reviewChannelRules": [{"paths": ["ui/*"], "channel": "#frontend"}]}
    )
    with (
        patch.object(
            PullRequestClient,
            "pull",
            AsyncMock(return_value={"head": {"sha": "head"}, "base": {"sha": "base"}}),
        ) as read,
        patch.object(PullRequestClient, "files", AsyncMock(return_value=[{"filename": "ui/a"}])),
        patch.object(routes, "require_repo_access_for_user", AsyncMock()) as access,
        patch.object(RepoSettings, "cached", AsyncMock(return_value=settings)),
        patch.object(HumanReviewRequest, "active_for", AsyncMock(return_value=None)),
        patch.object(routes, "review_blockers", AsyncMock(return_value=None)),
    ):
        async with GitHubClient.as_app("lc", "repo") as github:
            pull = github.repo("lc", "repo").pull_request(7)
            assert await settings.channel_for_pr(pull) == "#frontend"
            read.side_effect = (
                httpx2.ReadTimeout("unavailable")
                if status is None
                else httpx2.HTTPStatusError(
                    "unavailable",
                    request=httpx2.Request("GET", "https://api.github.com"),
                    response=httpx2.Response(
                        status,
                        request=httpx2.Request("GET", "https://api.github.com"),
                        headers={"X-RateLimit-Remaining": "0"} if limited else {},
                    ),
                )
            )
            with pytest.raises(httpx2.HTTPError):
                await settings.channel_for_pr(pull)
            if status in (None, 429, 503) or limited:
                result = await routes.api_human_review_availability(
                    "lc", "repo", 7, session={"sub": "ada"}
                )
                assert result.available
                access.side_effect = HTTPException(403, "repository access denied")
                with pytest.raises(HTTPException) as denied:
                    await routes.api_human_review_availability(
                        "lc", "repo", 7, session={"sub": "ada"}
                    )
                assert denied.value.status_code == 403
            else:
                with pytest.raises(httpx2.HTTPStatusError):
                    await settings.channel_for_pr(pull, for_display=True)
                with pytest.raises(HTTPException) as unavailable:
                    await routes.api_human_review_availability(
                        "lc", "repo", 7, session={"sub": "ada"}
                    )
                assert unavailable.value.status_code == 404
            updated = settings.model_copy(deep=True)
            updated.review_channel_rules[0].channel = "#design"
            with pytest.raises(httpx2.HTTPError):
                await updated.channel_for_pr(pull, for_display=True)
