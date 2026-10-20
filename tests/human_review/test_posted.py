import asyncio
from unittest.mock import AsyncMock

import pytest

from openswe.database import postgres
from openswe.expedited_review.eligibility import ChangedFile
from openswe.expedited_review.readiness import PullRequestSnapshot, Readiness
from openswe.github.codeowners import CodeOwners
from openswe.github.pull_requests import PullRequest
from openswe.human_review import lifecycle, posted, standard
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.posted import linked_pull_requests
from openswe.human_review.requests import HumanReviewRequest
from openswe.users import User, UserIdentity, UserPreferences
from tests.support.slack_api import SlackAPI


def test_links_resolve_and_deduplicate_only_on_our_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example.com")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "")
    refs = linked_pull_requests(
        "<https://github.com/lc/repo/pull/7|lc/repo#7> "
        "https://github.com/LC/repo/pull/7/files "
        "<https://openswe.example.com/agents/reviews/lc/repo/7|review> "
        "https://openswe.example.com/agents/reviews/lc/repo/8?tab=files "
        "https://untrusted.example.com/agents/reviews/lc/repo/9 "
        "https://user@openswe.example.com/agents/reviews/lc/repo/10 "
        "https://[malformed"
    )
    assert {ref.url.lower() for ref in refs} == {
        "https://github.com/lc/repo/pull/7",
        "https://github.com/lc/repo/pull/8",
    }


async def test_external_authors_are_not_watched(
    monkeypatch: pytest.MonkeyPatch, github_app: AsyncMock
) -> None:
    from openswe.github.pull_requests import PullRequestPayload

    monkeypatch.setattr(posted, "skip_on_preview", lambda _: False)
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=User()))
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=None))
    monkeypatch.setattr(HumanReviewRequest, "active_for", AsyncMock(return_value=None))
    details = PullRequestPayload.model_validate({"user": {"login": "external"}, "state": "open"})
    monkeypatch.setattr(
        posted,
        "record_pull_request",
        AsyncMock(return_value=(PullRequest(owner="lc", repo="repo", number=7), details)),
    )
    save = AsyncMock()
    monkeypatch.setattr(HumanReviewRequest, "save", save)
    await posted.watch_post("C1", "1.0", "U1", "https://github.com/lc/repo/pull/7")
    save.assert_not_called()


async def test_blocked_reactions_track_an_approved_posts_current_head(
    registry_db: None, slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch, github_app: AsyncMock
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
    monkeypatch.setattr(Readiness, "assess", AsyncMock(return_value=Readiness(snapshot, [])))
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
            if method == "reactions.remove" and params["name"] != "white_check_mark"
        }
        assert removed == {"x", "construction"} - expected
        assert all(
            params["channel"] == "C1" and params["timestamp"] == "1.0"
            for _, params in slack_api.calls[start:]
        )

    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=User()))
    monkeypatch.setattr(CodeOwners, "fetch", AsyncMock(return_value=CodeOwners.parse("* @ada")))
    monkeypatch.setattr(
        ChangedFile, "of_pull", AsyncMock(return_value=[ChangedFile(filename="app.py")])
    )
    await settle_with_reactions(set())
    stored = await HumanReviewRequest.get(request.id)
    assert stored is not None and stored.approved_at is None
    monkeypatch.setattr(CodeOwners, "fetch", AsyncMock(return_value=CodeOwners.parse("* @grace")))
    await settle_with_reactions(set())
    stored = await HumanReviewRequest.get(request.id)
    assert stored is not None and stored.approved_at is not None

    async def unreadable(*_: object, strict: bool = False) -> None:
        if strict:
            raise standard.RepoFileUnreadableError("unreadable")

    monkeypatch.setattr(CodeOwners, "fetch", unreadable)
    await settle_with_reactions(set())
    stored = await HumanReviewRequest.get(request.id)
    assert stored is not None and stored.approved_at is None
    monkeypatch.setattr(CodeOwners, "fetch", AsyncMock(return_value=None))
    await settle_with_reactions(set())
    stored = await HumanReviewRequest.get(request.id)
    assert stored is not None and stored.approved_at is not None
    monkeypatch.setattr(CodeOwners, "fetch", AsyncMock(return_value=CodeOwners.parse("* @ada")))
    await settle_with_reactions(set())

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


async def test_multiple_prs_need_all_approvals_but_any_blocker_and_explain_failures(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(identities=[UserIdentity(provider="slack", external_id="U1")])
    async with postgres.session() as session:
        session.add(user)
    urls = [f"https://github.com/lc/repo/pull/{number}" for number in (7, 8)]
    requests: list[HumanReviewRequest] = []
    for number in (7, 8):
        pr = await PullRequest(owner="lc", repo="repo", number=number).save()
        requests.append(
            await HumanReviewRequest(
                pull_request_id=pr.id,
                head_sha="abc",
                kind="posted",
                requested_by_user_id=user.id,
                slack_channel_id="C1",
                slack_message_ts="1.0",
                run_config={"posted_pr_urls": urls},
            ).save()
        )
    reactions: set[str] = set()

    async def add(channel: str, timestamp: str, emoji: str) -> bool:
        reactions.add(emoji)
        return True

    async def remove(channel: str, timestamp: str, emoji: str) -> bool:
        reactions.discard(emoji)
        return True

    wake = AsyncMock()
    monkeypatch.setattr(lifecycle, "add_slack_reaction", add)
    monkeypatch.setattr(lifecycle, "remove_slack_reaction", remove)
    monkeypatch.setattr(lifecycle, "wake_thread_owner", wake)
    monkeypatch.setattr(
        User,
        "preferences_for_login",
        AsyncMock(return_value=UserPreferences(pr_failure_reactions=True)),
    )
    first, second = map(ReviewCard, requests)
    await first.mark_approved()
    assert "white_check_mark" not in reactions
    await second.mark_approved()
    assert "white_check_mark" in reactions
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
        failing_checks=["unit tests"],
        unresolved_threads=0,
    )
    await first.update_blocked_reactions(snapshot)
    assert {"x", "construction"} <= reactions
    assert wake.await_count == 1
    args = wake.await_args.args
    assert args[:3] == ("C1", "1.0", "U1")
    assert urls[0] in args[3] and "unit tests" in args[3]
    await first.update_blocked_reactions(snapshot)
    assert wake.await_count == 1
    snapshot.mergeable = True
    snapshot.mergeable_state = "clean"
    snapshot.check_state = "success"
    await second.update_blocked_reactions(snapshot)
    assert {"x", "construction"} <= reactions
    await first.update_blocked_reactions(snapshot)
    assert not reactions & {"x", "construction"}
    async with HumanReviewRequest.locked(requests[1].id) as (_, row):
        assert row is not None
        row.approved_at = None
    await second.update_post_reactions()
    assert "white_check_mark" not in reactions
    snapshot.check_state = "failure"
    await second.update_blocked_reactions(snapshot)
    assert wake.await_count == 2
    assert urls[1] in wake.await_args.args[3]


async def test_each_owned_path_needs_an_approval_including_team_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.github import codeowners

    monkeypatch.setattr(codeowners, "team_members", AsyncMock(return_value=["Grace"]))
    owners = CodeOwners.parse("* @ada\n/api/ @lc/backend @bob\n/docs/\n")
    paths = ["app.py", "api/routes.py", "docs/guide.md"]
    assert not await owners.approved_by(paths, {"ada"})
    assert not await owners.approved_by(paths, {"grace"})
    assert await owners.approved_by(paths, {"ADA", "grace"})
    assert await owners.approved_by(paths, {"ada", "bob"})


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
        await ReviewCard(request).retire("merged", "merged")

    monkeypatch.setattr(lifecycle, "add_slack_reaction", add)
    monkeypatch.setattr(lifecycle, "remove_slack_reaction", remove)
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(ReviewCard(request).update_blocked_reactions(snapshot))
        await asyncio.wait_for(adding.wait(), timeout=5)
        tasks.create_task(retire())
        await retiring.wait()
        release.set()
    assert not reactions & {"x", "construction"}
    await ReviewCard(request).update_blocked_reactions(snapshot)
    assert not reactions & {"x", "construction"}
