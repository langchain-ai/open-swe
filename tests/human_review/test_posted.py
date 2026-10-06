import asyncio
from unittest.mock import AsyncMock

import pytest

from agent.expedited_review.readiness import PullRequestSnapshot, Readiness
from agent.github.pull_requests import PullRequest
from agent.human_review import lifecycle, standard
from agent.human_review.posted import linked_pull_request
from agent.human_review.requests import HumanReviewRequest
from agent.users import User, UserPreferences
from tests.support.slack_api import SlackAPI


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


async def test_blocked_reactions_track_an_approved_posts_current_head(
    registry_db: None, slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    pr = await PullRequest(owner="lc", repo="repo", number=7).save()
    request = await HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="posted",
        slack_channel_id="C1",
        slack_message_ts="1.0",
    ).save()
    snapshot = PullRequestSnapshot(
        state="open",
        merged=False,
        draft=False,
        head_sha="abc",
        title="Fix",
        author="ada",
        mergeable=None,
        mergeable_state="unknown",
        check_state="pending",
        unresolved_threads=0,
    )
    monkeypatch.setattr(standard, "repo_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        standard, "assess_readiness", AsyncMock(return_value=Readiness(snapshot, []))
    )
    monkeypatch.setattr(
        standard, "latest_review_states", AsyncMock(return_value={"grace": "APPROVED"})
    )

    async def settle_with_reactions(expected: set[str]) -> None:
        start = len(slack_api.calls)
        assert await standard.settle(request)
        reactions = {
            params["name"]
            for method, params in slack_api.calls[start:]
            if method == "reactions.add" and params["name"] != "white_check_mark"
        }
        assert reactions == expected
        removed = {
            params["name"]
            for method, params in slack_api.calls[start:]
            if method == "reactions.remove"
        }
        assert removed == {"x", "construction"} - expected
        assert all(
            params["channel"] == "C1" and params["timestamp"] == "1.0"
            for _, params in slack_api.calls[start:]
        )

    preferences = UserPreferences()

    async def owner_preferences(login: str) -> UserPreferences:
        assert login == "ada"
        return preferences

    monkeypatch.setattr(User, "preferences_for_login", owner_preferences)
    await settle_with_reactions(set())
    snapshot.check_state = "failure"
    await settle_with_reactions(set())
    snapshot.mergeable = False
    await settle_with_reactions({"construction"})
    snapshot.mergeable = None
    preferences.pr_failure_reactions = True
    await settle_with_reactions({"x"})
    preferences.pr_failure_reactions = False
    await settle_with_reactions(set())
    preferences.pr_failure_reactions = True
    snapshot.mergeable = False
    snapshot.mergeable_state = "dirty"
    await settle_with_reactions({"x", "construction"})
    snapshot.check_state = "success"
    await settle_with_reactions({"construction"})
    snapshot.mergeable = True
    snapshot.mergeable_state = "clean"
    await settle_with_reactions(set())
    snapshot.check_state = "failure"
    snapshot.merged = True
    snapshot.state = "closed"
    await settle_with_reactions({"merged"})
    stored = await HumanReviewRequest.get(request.id)
    assert stored is not None and stored.state == "merged"


async def test_retirement_clears_in_flight_and_stale_blockers(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pr = await PullRequest(owner="lc", repo="repo", number=7).save()
    request = await HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="posted",
        slack_channel_id="C1",
        slack_message_ts="1.0",
    ).save()
    snapshot = PullRequestSnapshot(
        state="open",
        merged=False,
        draft=False,
        head_sha="abc",
        title="Fix",
        author="ada",
        mergeable=False,
        mergeable_state="dirty",
        check_state="failure",
        unresolved_threads=0,
    )
    adding = asyncio.Event()
    release = asyncio.Event()
    retiring = asyncio.Event()
    reactions: set[str] = set()

    async def add(channel: str, timestamp: str, emoji: str) -> bool:
        adding.set()
        await release.wait()
        reactions.add(emoji)
        return True

    async def remove(channel: str, timestamp: str, emoji: str) -> bool:
        reactions.discard(emoji)
        return True

    async def retire() -> None:
        retiring.set()
        await lifecycle.retire(request, "merged", "merged")

    monkeypatch.setattr(lifecycle, "add_slack_reaction", add)
    monkeypatch.setattr(lifecycle, "remove_slack_reaction", remove)
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(lifecycle.update_blocked_reactions(request, snapshot))
        await asyncio.wait_for(adding.wait(), timeout=5)
        tasks.create_task(retire())
        await retiring.wait()
        release.set()
    assert not reactions
    await lifecycle.update_blocked_reactions(request, snapshot)
    assert not reactions
