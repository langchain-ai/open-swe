import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import httpx2
import pytest

from openswe.expedited_review.eligibility import ChangedFile
from openswe.expedited_review.readiness import PullRequestSnapshot
from openswe.github.http import GitHubClient, GitHubError, RepoClient
from openswe.github.pull_requests import PullRequest, ThreadLink
from openswe.github.repo_files import RepoSettings
from openswe.human_review.lifecycle import ReviewCard, ReviewPicks
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from openswe.human_review.standard import (
    SUMMARY_MAX_CHARS,
    merge_wait,
    request_blockers,
    review_reminder_at,
    summary_line,
)
from openswe.slack.blocks import block_payload, section
from openswe.slack.http import SlackRequestError
from openswe.users import User, UserIdentity
from tests.conftest import FakeUserRecords

_NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


async def test_review_backlink_survives_card_reposts_without_duplicate_comments() -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    request = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="standard",
        slack_channel_id="C1",
        slack_thread_ts="1.0",
        slack_message_ts="2.0",
    )
    request.pull_request = pr
    pull = AsyncMock()
    pull.issue_comments.side_effect = [
        [],
        [{"body": f"<!-- open-swe-review-request:{request.id} -->"}],
    ]
    with (
        patch("openswe.human_review.lifecycle.PullRequestClient.as_app") as app,
        patch(
            "openswe.human_review.lifecycle.get_slack_permalink",
            AsyncMock(return_value="https://slack.example.com/review"),
        ),
    ):
        app.return_value.__aenter__.return_value = pull
        await ReviewCard(request).link_on_github()
        request.slack_message_ts = "3.0"
        await ReviewCard(request).link_on_github()
    pull.comment.assert_awaited_once_with(
        f"Review requested in [Slack](https://slack.example.com/review).\n\n"
        f"<!-- open-swe-review-request:{request.id} -->"
    )


@pytest.mark.parametrize("decision", [None, "picked", "review"])
async def test_concurrent_picks_add_at_most_one_reviewer(decision: str | None) -> None:
    from openswe.human_review.people import Outcome, Participant
    from openswe.human_review.standard import _add_reviewer

    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    if decision is not None:
        request.participants.append(
            HumanReviewParticipant(
                user_id=User().id, decision="picked" if decision == "picked" else "review"
            )
        )
    lock = asyncio.Lock()

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        async with lock:
            yield None, request

    with (
        patch.object(HumanReviewRequest, "locked", locked),
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch.object(ReviewCard, "refresh", AsyncMock()),
    ):
        results = await asyncio.gather(
            *(
                _add_reviewer(request, Participant(User(), login), picked=True)
                for login in ("grace", "linus")
            )
        )
    assert len(request.participants) == 1
    assert sum(not isinstance(result, Outcome) for result in results) == (
        1 if decision is None else 0
    )


@pytest.mark.parametrize("updated", [True, False])
async def test_completed_pick_marks_dm_stale_and_preserves_reviewer(updated: bool) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    user = User(identities=[UserIdentity(provider="github", external_id="grace", login="Grace")])
    pick = HumanReviewParticipant(
        user_id=user.id,
        decision="picked",
        assigned_by_agent=True,
        dm_channel_id="D1",
        dm_ts="1.2",
        dm_text="Accept this review",
    )
    pick.user = user
    request.participants.append(pick)

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        yield None, request

    with (
        patch.object(HumanReviewRequest, "locked", locked),
        patch(
            "openswe.human_review.pick_message.update_slack_message",
            AsyncMock(side_effect=None if updated else SlackRequestError("rate_limited")),
        ) as update,
    ):
        await ReviewPicks(request).complete({"grace"})
        text = f"Review submitted for <{pr.url}|{pr.label}>."
        update.assert_awaited_once_with("D1", "1.2", text, blocks=block_payload([section(text)]))
        assert request.participants == [pick]
        assert pick.decision == ("review" if updated else "picked")
        assert pick.dm_ts == ("" if updated else "1.2")
        await ReviewPicks(request).complete({"grace"})
        assert update.await_count == (1 if updated else 2)


async def test_decline_only_withdraws_the_users_pending_pick() -> None:
    from openswe.human_review.standard import decline

    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    picked = User()
    participant = HumanReviewParticipant(user_id=picked.id, decision="picked")
    participant.user = picked
    request.participants.append(participant)

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        yield None, request

    with (
        patch.object(HumanReviewRequest, "locked", locked),
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch(
            "openswe.human_review.lifecycle.Repository.resolve_default_branch",
            AsyncMock(return_value=""),
        ),
        patch.object(ReviewCard, "refresh", AsyncMock()),
        patch("openswe.human_review.standard.start_auto_assign", AsyncMock()) as rotate,
    ):
        await decline(request, User(), "Away or unavailable")
        assert participant.decision == "picked"
        rotate.assert_not_awaited()
        await decline(request, picked, "Away or unavailable")
        assert participant.decision == "expired"
        rotate.assert_awaited_once()
        await decline(request, picked, "Away or unavailable")
        assert rotate.await_count == 1


async def test_snoozed_pick_does_not_expire_before_its_new_deadline() -> None:
    from openswe.human_review.standard import expire_picks, snooze

    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    user = User()
    pick = HumanReviewParticipant(user_id=user.id, decision="picked")
    pick.user = user
    request.participants.append(pick)

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        yield None, request

    with (
        patch.object(HumanReviewRequest, "locked", locked),
        patch("openswe.human_review.standard._schedule", AsyncMock(return_value=True)),
        patch("openswe.human_review.standard._assignment_minutes", AsyncMock(return_value=120)),
    ):
        await snooze(request, User(), "1 hour")
        assert pick.joined_at is None
        await snooze(request, user, "2 days")
        assert pick.joined_at is not None and pick.joined_at > datetime.now(UTC)
        assert await expire_picks(request) == "accepted"
        assert pick.decision == "picked"


@pytest.mark.parametrize("author", ["ada", "open-swe[bot]", "langsmith-engine[bot]"])
async def test_losing_auto_assignment_does_not_wake_another_picker(author: str) -> None:
    from openswe.human_review.picking import Pick
    from openswe.human_review.standard import RequestResult, _auto_assign

    pr = PullRequest(owner="lc", repo="repo", number=7, author=author)
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    with (
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch.object(
            User, "for_login", AsyncMock(return_value=User() if author == "ada" else None)
        ),
        patch.object(GitHubClient, "as_app", _review_github_app),
        patch.object(
            RepoClient,
            "read_file",
            AsyncMock(return_value='{"reviewAllowedAuthors": ["langsmith-engine[bot]"]}'),
        ),
        patch(
            "openswe.human_review.standard.choose_reviewer",
            AsyncMock(return_value=Pick("grace", "owner")),
        ),
        patch(
            "openswe.human_review.standard.assign",
            AsyncMock(return_value=RequestResult(success=False, claimed=True)),
        ),
        patch("openswe.human_review.standard._wake_picker", AsyncMock()) as wake,
    ):
        assert (await _auto_assign(request, asked=True, trigger=None)).status == "claimed"
    wake.assert_not_awaited()


@pytest.mark.parametrize("source", ["automatic", "named", "forged", "mcp", "unauthorized_mcp"])
async def test_opted_out_reviewer_requires_a_verified_person_naming_them(
    source: str, user_records: FakeUserRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openswe.human_review import standard
    from openswe.human_review.people import Participant
    from openswe.human_review.picking import Coverage

    tool = import_module("openswe.tools.request_human_review")
    reviewer = User(identities=[UserIdentity(provider="github", external_id="2", login="grace")])
    author = User(identities=[UserIdentity(provider="github", external_id="1", login="ada")])
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(
        pull_request_id=pr.id, head_sha="abc", kind="standard", thread_id="picker"
    )
    request.pull_request = pr
    user_records.seed("profile", "grace", {"auto_human_review_enabled": False})
    config: dict[str, object] = {
        "thread_id": "picker",
        "source": "slack",
        "slack_thread": {
            "channel_id": "C1",
            "thread_ts": "1.0",
            "triggering_event_ts": "2.0",
            "triggering_user_id": "U_ADA",
        },
    }
    if source in {"mcp", "unauthorized_mcp"}:
        config.update(
            source="mcp", thread_id=None, github_login="ada" if source == "mcp" else "grace"
        )
    monkeypatch.setattr(tool, "get_config", lambda: {"configurable": config})
    monkeypatch.setattr(
        tool,
        "fetch_slack_thread_message_by_ts",
        AsyncMock(
            return_value={
                "user": "U_ADA",
                "text": "assign @grace" if source == "named" else "who is reviewing?",
            }
        ),
    )
    monkeypatch.setattr(tool, "_repository_refusal", AsyncMock(return_value=None))
    monkeypatch.setattr(
        User,
        "for_login",
        AsyncMock(side_effect=lambda _, login: author if login == "ada" else reviewer),
    )
    monkeypatch.setattr(HumanReviewRequest, "active_for", AsyncMock(return_value=request))
    monkeypatch.setattr(HumanReviewRequest, "get", AsyncMock(return_value=request))
    monkeypatch.setattr(standard, "_github_approvers", AsyncMock(return_value=[]))
    monkeypatch.setattr(Coverage, "load", AsyncMock(return_value=None))
    monkeypatch.setattr(
        standard, "resolve_writer", AsyncMock(return_value=Participant(reviewer, "grace"))
    )
    monkeypatch.setattr(ReviewCard, "refresh", AsyncMock())
    monkeypatch.setattr(standard, "_request_github_review", AsyncMock(return_value=True))
    monkeypatch.setattr(standard, "_schedule", AsyncMock(return_value=True))
    monkeypatch.setattr(standard, "_assignment_minutes", AsyncMock(return_value=120))
    monkeypatch.setattr(standard, "_permalink", AsyncMock(return_value=""))

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        yield None, request

    monkeypatch.setattr(HumanReviewRequest, "locked", locked)
    result = await tool.assign_human_reviewer(
        pr.url, "grace", "owner", named_by_person=source in {"named", "forged"}
    )
    assert result["success"] is (source in {"named", "mcp"})
    if source in {"named", "mcp"}:
        assert len(request.participants) == 1
        assert request.participants[0].user_id == reviewer.id
        assert request.participants[0].decision == "picked"
    else:
        assert not request.participants
        if source == "automatic":
            assert "opted out" in result["error"]


@pytest.mark.parametrize(
    ("local_time", "out_of_office", "named", "allowed"),
    [
        ("2026-10-22T03:00:00", False, False, False),
        ("2026-10-22T09:00:00", False, False, True),
        ("2026-10-22T12:00:00", True, False, False),
        ("2026-10-22T03:00:00", False, True, True),
    ],
)
async def test_assignment_enforces_availability_before_contacting_reviewer(
    local_time: str, out_of_office: bool, named: bool, allowed: bool
) -> None:
    from openswe.human_review import standard
    from openswe.human_review.people import Participant
    from openswe.human_review.picking import Coverage, ReviewerAvailability

    zone = ZoneInfo("America/New_York")
    now = datetime.fromisoformat(local_time).replace(tzinfo=zone).astimezone(UTC)
    user = User(
        identities=[
            UserIdentity(provider="github", external_id="grace", login="grace"),
            UserIdentity(provider="slack", external_id="U_GRACE", login="grace"),
        ]
    )
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        yield None, request

    with (
        patch.object(standard, "datetime", wraps=datetime) as clock,
        patch.object(standard, "_github_approvers", AsyncMock(return_value=[])),
        patch.object(standard.PullRequestClient, "as_app"),
        patch.object(
            standard,
            "choose_reviewer",
            AsyncMock(
                return_value=standard.Pick(
                    "other", "recent contributor", frozenset({"other", "grace"})
                )
            ),
        ),
        patch.object(ChangedFile, "of_pull", AsyncMock(return_value=[])),
        patch.object(Coverage, "load", AsyncMock(return_value=None)),
        patch.object(User, "for_login", AsyncMock(return_value=user)),
        patch.object(standard.PROFILES, "allows_auto_human_review", AsyncMock(return_value=True)),
        patch.object(
            ReviewerAvailability,
            "for_user",
            AsyncMock(return_value=ReviewerAvailability(zone, out_of_office)),
        ),
        patch.object(
            standard, "resolve_writer", AsyncMock(return_value=Participant(user, "grace"))
        ),
        patch.object(HumanReviewRequest, "locked", locked),
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch.object(ReviewCard, "refresh", AsyncMock()),
        patch.object(standard, "_request_github_review", AsyncMock()) as github_review,
        patch.object(standard, "send_dm_with_location", AsyncMock(return_value=None)) as dm,
        patch.object(standard, "_schedule", AsyncMock(return_value=True)) as schedule,
        patch.object(standard, "_assignment_minutes", AsyncMock(return_value=120)),
        patch.object(standard, "_permalink", AsyncMock(return_value="")),
    ):
        clock.now.return_value = now
        result = await standard.assign(request, "grace", "nearby reviewer", replace=named)

    assert result.success is allowed
    assert bool(request.participants) is allowed
    if allowed:
        github_review.assert_awaited_once()
        dm.assert_awaited_once()
    else:
        assert ("out of office" if out_of_office else "outside working hours") in result.error
        github_review.assert_not_awaited()
        dm.assert_not_awaited()
        schedule.assert_not_awaited()


@pytest.mark.parametrize("status", [200, 503])
async def test_reviewer_removal_sends_delete_body_without_aborting(
    status: int, github_app: AsyncMock
) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    grace = User(identities=[UserIdentity(provider="github", external_id="grace", login="grace")])
    pick = HumanReviewParticipant(user_id=grace.id, decision="picked")
    pick.user = grace
    request.participants.append(pick)
    received: list[tuple[str, str, object]] = []

    async def respond(_client: object, method: str, url: str, **kwargs: object) -> httpx2.Response:
        received.append((method, url, kwargs.get("json")))
        return httpx2.Response(status, json={}, request=httpx2.Request(method, url))

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        yield None, request

    with (
        patch.object(HumanReviewRequest, "locked", locked),
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch.object(ReviewCard, "refresh", AsyncMock()),
        patch("openswe.github.http.github_request", respond),
    ):
        dropped = await ReviewPicks(request).drop({grace.id}, "Withdrawn.")
    assert dropped == [pick]
    assert received == [
        (
            "DELETE",
            "https://api.github.com/repos/lc/repo/pulls/7/requested_reviewers",
            {"reviewers": ["grace"]},
        )
    ]


def _snapshot(**overrides: object) -> PullRequestSnapshot:
    base = PullRequestSnapshot(
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
    for name, value in overrides.items():
        setattr(base, name, value)
    return base


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        ("2026-09-28T08:00:00", "2026-09-28T11:00:00"),
        ("2026-09-28T17:00:00", "2026-09-29T10:00:00"),
        ("2026-10-30T17:00:00", "2026-11-02T10:00:00"),
        ("2026-10-31T12:00:00", "2026-11-02T11:00:00"),
    ],
)
def test_review_reminders_count_only_local_business_hours(start: str, expected: str) -> None:
    timezone = ZoneInfo("America/New_York")
    assert review_reminder_at(datetime.fromisoformat(start).replace(tzinfo=timezone), timezone) == (
        datetime.fromisoformat(expected).replace(tzinfo=timezone).astimezone(UTC)
    )


def test_a_ready_pull_request_can_be_put_up_for_review() -> None:
    assert request_blockers(_snapshot()) == []


@pytest.mark.parametrize("check_state", ["pending", "failure", "blocked"])
def test_checks_conflicts_and_unresolved_threads_do_not_block_the_request(check_state: str) -> None:
    assert (
        request_blockers(
            _snapshot(
                check_state=check_state,
                mergeable=False,
                mergeable_state="dirty",
                failing_checks=["lint"],
                unresolved_threads=3,
            )
        )
        == []
    )


@pytest.mark.parametrize(
    ("overrides", "blocker"),
    [
        ({"draft": True}, "draft"),
        ({"state": "closed"}, "closed"),
    ],
)
def test_requests_are_refused_while_the_pull_request_is_not_reviewable(
    overrides: dict[str, object], blocker: str
) -> None:
    assert any(blocker in reason for reason in request_blockers(_snapshot(**overrides)))


@pytest.mark.parametrize("minutes,expected", [(120, "waiting"), (15, "woken")])
async def test_unclaimed_deadline_honors_workspace_timeout(minutes: int, expected: str) -> None:
    from openswe.human_review.standard import AutoAssignResult, run_deadline

    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="standard",
        state="open",
    )
    request.created_at = datetime.now(UTC) - timedelta(minutes=30)
    request.pull_request = pr
    with (
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch("openswe.human_review.standard._assignment_minutes", AsyncMock(return_value=minutes)),
        patch("openswe.human_review.standard._schedule", AsyncMock(return_value=True)),
        patch("openswe.human_review.standard._github_approvers", AsyncMock(return_value=[])),
        patch(
            "openswe.human_review.standard.start_auto_assign",
            AsyncMock(return_value=AutoAssignResult("woken")),
        ),
    ):
        assert await run_deadline(str(request.id), "unclaimed") == {"status": expected}


def test_nothing_merges_without_an_approval() -> None:
    long_ago = _NOW - timedelta(days=1)
    assert merge_wait([], long_ago, {}, _NOW) == "an approval on GitHub"
    assert merge_wait(["grace"], long_ago, {"grace": "CHANGES_REQUESTED"}, _NOW) is not None


def test_every_reviewer_approving_merges_before_the_deadline() -> None:
    states = {"grace": "APPROVED", "Linus": "APPROVED"}
    assert merge_wait(["grace", "linus"], _NOW, states, _NOW) is None


@pytest.mark.parametrize("auto_merge", [False, True])
def test_one_approval_waits_for_the_other_reviewers_until_the_deadline(auto_merge: bool) -> None:
    states = {"grace": "APPROVED"}
    early = _NOW - timedelta(hours=1)
    late = _NOW - timedelta(hours=2)

    assert merge_wait(["grace", "linus"], early, states, _NOW) == "approval from @linus"
    assert merge_wait(["grace", "linus"], late, states, _NOW, auto_merge=auto_merge) == (
        None if auto_merge else "approval from @linus"
    )


def test_an_approval_from_someone_who_did_not_sign_up_counts() -> None:
    assert merge_wait([], _NOW, {"hopper": "APPROVED"}, _NOW) is None


@pytest.mark.parametrize(
    "states",
    [
        {"Grace": "APPROVED", "hopper": "APPROVED", "unknown": "APPROVED", "linus": "DISMISSED"},
        None,
    ],
)
async def test_merged_card_names_only_actual_approvers(
    states: dict[str, str] | None, github_app: AsyncMock
) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    for login in ("grace", "linus"):
        user = User(
            identities=[
                UserIdentity(provider="github", external_id=login, login=login),
                UserIdentity(provider="slack", external_id=f"U_{login}"),
            ]
        )
        participant = HumanReviewParticipant(user_id=user.id, decision="review")
        participant.user = user
        request.participants.append(participant)
    hopper = User(identities=[UserIdentity(provider="slack", external_id="U_hopper")])
    with (
        patch(
            "openswe.human_review.lifecycle.latest_review_states", AsyncMock(return_value=states)
        ),
        patch.object(
            User,
            "for_login",
            AsyncMock(side_effect=lambda provider, login: hopper if login == "hopper" else None),
        ),
    ):
        text, blocks = await ReviewCard(request).render("merged")
    payload = block_payload(blocks)
    assert payload is not None
    rendered = str(payload)
    for message in (text, rendered):
        assert "merged" in message
        assert "by @ada" in message
        if states:
            assert "approved by <@U_grace>, <@U_hopper>, @unknown" in message
        else:
            assert "approved by" not in message
        assert "linus" not in message


@pytest.mark.parametrize("available", [True, False])
async def test_open_card_counts_only_non_test_files(available: bool) -> None:
    pr = PullRequest(
        owner="o",
        repo="r",
        number=1,
        title="Fix",
        author="ada",
        additions=110,
        deletions=55,
        changed_files=2,
    )
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    request.requested_by = None
    files = [
        ChangedFile(filename="openswe/example.py", additions=10, deletions=5),
        ChangedFile(filename="tests/test_example.py", additions=100, deletions=50),
    ]
    with (
        patch.object(ReviewCard, "_files", AsyncMock(return_value=files if available else None)),
        patch.object(ReviewCard, "_review_states", AsyncMock(return_value={})),
        patch.object(HumanReviewRequest, "author_mention", AsyncMock(return_value="@ada")),
    ):
        _, blocks = await ReviewCard(request)._render_standard(None)
    rendered = str(block_payload(blocks))
    assert ("+10 −5 in 1 non-test file" in rendered) is available
    assert "+110" not in rendered
    assert "−55" not in rendered


@pytest.mark.parametrize("count", [101, 102])
async def test_standard_card_requires_complete_file_counts(
    count: int, github_app: AsyncMock
) -> None:
    pr = PullRequest(owner="o", repo="r", number=1, changed_files=count)
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    files = [ChangedFile(filename=f"openswe/file_{i}.py") for i in range(101)]
    with patch.object(ChangedFile, "of_pull", AsyncMock(return_value=files)):
        result = await ReviewCard(request)._files()
    assert result == (files if count == 101 else None)


def test_a_short_description_is_shown_whole_without_its_template_comments() -> None:
    assert summary_line("<!-- template -->\nFixes the\nretry loop.\n") == "Fixes the retry loop."


def test_a_long_description_is_cut_at_a_word_with_an_ellipsis() -> None:
    description = "Retries failed uploads, " * 40
    line = summary_line(description)
    kept = line.removesuffix("…")
    assert line.endswith("…")
    assert len(line) <= SUMMARY_MAX_CHARS
    assert description.startswith(kept)
    assert description[len(kept)] in {" ", ","}


@pytest.mark.parametrize(
    ("states", "collapsed", "auto_merge", "pending"),
    [
        ({"grace": "APPROVED"}, True, True, False),
        ({"grace": "APPROVED"}, True, False, False),
        ({"grace": "APPROVED"}, False, False, True),
        ({"grace": "APPROVED"}, True, True, True),
        ({}, False, True, False),
        ({"grace": "APPROVED", "linus": "CHANGES_REQUESTED"}, False, True, False),
    ],
)
async def test_approved_card_collapses_without_closing_the_request(
    states: dict[str, str], collapsed: bool, auto_merge: bool, pending: bool, github_app: AsyncMock
) -> None:
    pr = PullRequest(owner="o", repo="r", number=1, title="Fix", author="ada")
    request = HumanReviewRequest(
        pull_request_id=pr.id, head_sha="abc", kind="standard", auto_merge=auto_merge
    )
    request.pull_request = pr
    request.requested_by = None
    request.created_at = datetime.now(UTC) - timedelta(hours=3)
    if pending:
        user = User(
            identities=[UserIdentity(provider="github", external_id="linus", login="linus")]
        )
        reviewer = HumanReviewParticipant(user_id=user.id, decision="review")
        reviewer.user = user
        request.participants.append(reviewer)
    with (
        patch(
            "openswe.human_review.lifecycle.latest_review_states", AsyncMock(return_value=states)
        ),
        patch.object(HumanReviewRequest, "author_mention", AsyncMock(return_value="<@U_ada>")),
    ):
        text, blocks = await ReviewCard(request).render(None)
    assert text.startswith(":white_check_mark: ") is collapsed
    assert (len(blocks) == 1) is collapsed
    assert "<@U_ada>" in str(block_payload(blocks))
    if collapsed:
        assert "\n" not in text
        assert "authored by <@U_ada> — approved by @grace" in text
    if pending and not auto_merge:
        assert "linus" in str(block_payload(blocks))
        assert "awaiting manual merge" not in text
    assert request.state == "open"


@pytest.mark.parametrize("in_thread", [False, True])
@pytest.mark.parametrize("outcome", [None, "merged"])
@pytest.mark.parametrize("source", ["unavailable", "moved", "detached"])
async def test_review_card_keeps_its_originating_slack_thread(
    in_thread: bool, outcome: str | None, source: str, github_app: AsyncMock
) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(
        pull_request_id=pr.id,
        head_sha="abc",
        kind="standard",
        thread_id="origin-thread",
        slack_channel_id="C_REVIEW",
        slack_thread_ts="123.456" if in_thread else "",
        run_config={"slack_thread": {"channel_id": "C_SOURCE", "thread_ts": "123.456"}},
    )
    request.pull_request = pr
    request.requested_by = None
    channel = "C_MOVED" if source == "moved" else "C_SOURCE"
    source_url = f"https://langchain.slack.com/archives/{channel}/p123456"
    client = MagicMock()
    client.threads.get = AsyncMock(
        return_value={
            "metadata": {
                "source_context": {
                    "slack_thread": {"channel_id": "C_MOVED", "thread_ts": "123.456"}
                }
            }
            if source == "moved"
            else {"slack_thread_detached_at": "2026-10-23T00:00:00Z"}
        },
        side_effect=RuntimeError("Metadata unavailable") if source == "unavailable" else None,
    )
    with (
        patch.object(ReviewCard, "_review_states", AsyncMock(return_value={})),
        patch.object(ReviewCard, "_warn_target", side_effect=lambda card: card),
        patch("openswe.slack.cards.get_client", return_value=client),
        patch(
            "openswe.slack.cards.get_slack_permalink",
            AsyncMock(
                side_effect=lambda target, ts: (
                    source_url if (target, ts) == (channel, "123.456") else None
                )
            ),
        ),
    ):
        _, blocks = await ReviewCard(request).render(outcome)
    rendered = str(block_payload(blocks))
    if source == "detached":
        assert "|Slack thread>" not in rendered
    else:
        assert f"<{source_url}|Slack thread>" in rendered


def _response(status: int, text: str = "") -> httpx2.Response:
    return httpx2.Response(
        status, text=text, request=httpx2.Request("GET", "https://api.github.com")
    )


def _repo() -> RepoClient:
    return GitHubClient(MagicMock()).repo("o", "r")


async def test_repo_settings_prefer_the_pull_request_head() -> None:
    request = AsyncMock(
        return_value=_response(200, '{"reviewChannel": "#eng-reviews", "other": 1}')
    )
    with patch("openswe.github.http.github_request", request):
        settings = await RepoSettings.fetch(_repo(), ref="abc123")
    assert settings.review_channel == "#eng-reviews"
    _client, _method, url = request.await_args.args
    assert url.endswith("/repos/o/r/contents/.open-swe/settings.json")
    assert request.await_args.kwargs["params"] == {"ref": "abc123"}


async def test_repo_settings_fall_back_to_the_default_branch() -> None:
    request = AsyncMock(
        side_effect=[_response(404), _response(200, '{"reviewChannel": "#eng-reviews"}')]
    )
    with patch("openswe.github.http.github_request", request):
        settings = await RepoSettings.fetch(_repo(), ref="abc123")
    assert settings.review_channel == "#eng-reviews"
    assert [call.kwargs["params"] for call in request.await_args_list] == [{"ref": "abc123"}, None]


@pytest.mark.parametrize(("status", "text"), [(404, ""), (200, "not json"), (200, "[]")])
async def test_missing_or_invalid_settings_have_no_review_channel(status: int, text: str) -> None:
    with patch(
        "openswe.github.http.github_request", AsyncMock(return_value=_response(status, text))
    ):
        assert (await RepoSettings.fetch(_repo())).review_channel == ""


@pytest.mark.usefixtures("registry_db")
async def test_assignment_inbox_is_personal_and_hides_completed_or_inaccessible_reviews(
    monkeypatch,
):
    from openswe.github.pull_requests import PullRequest
    from openswe.human_review.requests import (
        HumanReviewParticipant,
        HumanReviewRequest,
        RequestKind,
        RequestState,
    )
    from openswe.human_review.routes import api_review_assignments
    from openswe.users import User

    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada,grace")
    ada = await User.sign_in("github", "1", login="ada")
    grace = await User.sign_in("github", "2", login="grace")
    cases: list[tuple[int, User, bool, RequestState, RequestKind]] = [
        (1, ada, True, "open", "standard"),
        (2, ada, False, "open", "standard"),
        (3, grace, True, "open", "standard"),
        (4, ada, True, "cancelled", "standard"),
        (5, ada, True, "open", "posted"),
        (6, ada, True, "open", "standard"),
    ]
    for number, user, assigned, state, kind in cases:
        pr = await PullRequest(
            owner="o", repo="hidden" if number == 6 else "r", number=number, title=f"PR {number}"
        ).save()
        await HumanReviewRequest(
            pull_request_id=pr.id,
            head_sha="abc",
            kind=kind,
            state=state,
            participants=[
                HumanReviewParticipant(
                    user_id=user.id, decision="review", assigned_by_agent=assigned
                )
            ],
        ).save()

    @asynccontextmanager
    async def as_user(login: str) -> AsyncIterator[GitHubClient]:
        yield GitHubClient(MagicMock())

    async def info(repo: RepoClient) -> dict[str, object]:
        if repo.name == "hidden":
            raise GitHubError(_response(404))
        return {}

    async def states(pull, author):
        return {"ada": "APPROVED"} if pull.number == 5 else {}

    with (
        patch("openswe.human_review.routes.GitHubClient.as_user", as_user),
        patch.object(RepoClient, "info", info),
        patch("openswe.human_review.routes.latest_review_states", states),
    ):
        result = await api_review_assignments(page=1, session={"sub": "ada"})
    assert [row.number for row in result.pull_requests] == [1]


@asynccontextmanager
async def _review_github_app(owner: str, repo: str) -> AsyncIterator[GitHubClient]:
    yield GitHubClient(MagicMock())


@pytest.mark.parametrize(
    ("author", "user", "draft", "allowed", "expected"),
    [
        ("ada", User(), False, [], []),
        ("ada", None, False, [], ["its author is not an Open SWE user or in reviewAllowedAuthors"]),
        ("open-swe[bot]", None, False, [], []),
        ("open-swe-preview[bot]", None, False, [], []),
        ("openswe-dev[bot]", None, False, [], []),
        (
            "another[bot]",
            None,
            False,
            ["langsmith-engine[bot]"],
            ["its author is not an Open SWE user or in reviewAllowedAuthors"],
        ),
        ("open-swe[bot]", None, True, [], ["it is a draft"]),
        ("LangSmith-Engine[bot]", None, False, [" langsmith-engine[bot] "], []),
        ("ada", None, False, ["ADA"], []),
        ("ada", None, True, ["ada"], ["it is a draft"]),
    ],
)
async def test_review_blockers_require_an_account_or_an_allowed_author(
    author: str, user: User | None, draft: bool, allowed: list[str], expected: list[str]
) -> None:
    from openswe.expedited_review.readiness import Readiness
    from openswe.human_review.standard import review_blockers

    readiness = Readiness(snapshot=_snapshot(author=author, draft=draft), blockers=[])
    settings = RepoSettings(review_allowed_authors=allowed).model_dump_json(by_alias=True)
    with (
        patch.object(Readiness, "assess", AsyncMock(return_value=readiness)),
        patch.object(User, "for_login", AsyncMock(return_value=user)),
        patch.object(GitHubClient, "as_app", _review_github_app),
        patch.object(RepoClient, "read_file", AsyncMock(return_value=settings)),
    ):
        blockers = await review_blockers(_repo().pull_request(7))
    assert blockers == expected


@pytest.mark.parametrize(
    "settings", [None, "unreadable", "not json", '{"reviewAllowedAuthors": "ada"}']
)
async def test_review_author_allowlist_requires_valid_default_branch_settings(
    settings: str | None,
) -> None:
    from openswe.expedited_review.readiness import Readiness
    from openswe.human_review.standard import review_blockers

    readiness = Readiness(snapshot=_snapshot(author="ada"), blockers=[])

    async def read_file(
        repo: RepoClient, path: str, ref: str | None, *, max_chars: int, strict: bool = False
    ) -> str | None:
        if ref is not None:
            return '{"reviewAllowedAuthors": ["ada"]}'
        if settings == "unreadable":
            from openswe.github.repo_files import RepoFileUnreadableError

            raise RepoFileUnreadableError("Could not read settings")
        return settings

    with (
        patch.object(Readiness, "assess", AsyncMock(return_value=readiness)),
        patch.object(User, "for_login", AsyncMock(return_value=None)),
        patch.object(GitHubClient, "as_app", _review_github_app),
        patch.object(RepoClient, "read_file", read_file),
    ):
        blockers = await review_blockers(_repo().pull_request(7))
    assert blockers == (
        ["its author is not an Open SWE user or in reviewAllowedAuthors"]
        if settings is None
        else None
    )


@pytest.mark.usefixtures("registry_db")
@pytest.mark.parametrize(
    ("default", "override", "expected"),
    [(True, None, True), (False, None, False), (True, False, False), (False, True, True)],
)
async def test_review_request_snapshots_merge_mode_and_preserves_it_on_reuse(
    default: bool,
    override: bool | None,
    expected: bool,
) -> None:
    from openswe.dashboard.workspace_settings import WorkspaceSettings
    from openswe.human_review import standard
    from openswe.human_review.standard import Origin
    from openswe.slack.channels import SlackChannel
    from openswe.slack.client import GitHubPrRef

    pr = await PullRequest(owner="lc", repo="repo", number=7, author="ada").save()
    pr = await pr.link_thread("implementation")
    details = MagicMock(head_sha="abc", body="Fix")
    ref = GitHubPrRef(owner="lc", repo="repo", number=7, url=pr.url)
    with (
        patch.object(standard, "review_blockers", AsyncMock(return_value=[])),
        patch.object(standard, "_target_channel", AsyncMock(return_value=SlackChannel(id="C1"))),
        patch.object(standard, "record_pull_request", AsyncMock(return_value=(pr, details))),
        patch.object(
            standard,
            "_workspace_settings",
            AsyncMock(return_value=WorkspaceSettings({"human_review_auto_merge_enabled": default})),
        ),
        patch.object(standard, "_assignment_minutes", AsyncMock(return_value=120)),
        patch.object(standard, "_schedule", AsyncMock(return_value=True)) as schedule,
        patch.object(standard, "_permalink", AsyncMock(return_value="")),
        patch.object(ReviewCard, "post_standard", AsyncMock(return_value="1.0")),
        patch.object(ReviewCard, "link_on_github", AsyncMock()),
        patch.object(
            standard.SupplementaryReviews,
            "policies",
            AsyncMock(side_effect=RuntimeError("repository settings unavailable")),
        ),
    ):
        result = await standard._request_review(
            ref, MagicMock(), Origin(), channel="", inline_summary="Fix", auto_merge=override
        )
        assert result.success and result.auto_merge is expected
        request = await HumanReviewRequest.active_for("lc", "repo", 7)
        assert request is not None and request.auto_merge is expected
        assert request.pull_request.thread_ids == ["implementation"]
        async with HumanReviewRequest.locked(request.id) as (_, locked):
            assert locked is not None
            assert locked.pull_request.thread_ids == ["implementation"]
        assert [call.args[1] for call in schedule.await_args_list] == (
            ["unclaimed", "auto_merge"] if expected else ["unclaimed"]
        )
        reused = await standard._request_review(
            ref, MagicMock(), Origin(), channel="", inline_summary="Fix"
        )
        assert reused.success and reused.reused and reused.auto_merge is expected
        refused = await standard._request_review(
            ref, MagicMock(), Origin(), channel="", inline_summary="Fix", auto_merge=not expected
        )
        assert not refused.success
        assert request.auto_merge is expected


@pytest.mark.parametrize("trigger", ["event", "deadline"])
@pytest.mark.parametrize(
    ("auto_merge", "pending", "activity", "design_only"),
    [
        (False, False, "idle", False),
        (False, True, "idle", False),
        (True, True, "idle", False),
        (True, False, "idle", True),
        *[
            (True, False, status, False)
            for status in ("idle", "busy", "pending", "running", "unknown", "unavailable")
        ],
    ],
)
@pytest.mark.parametrize("author", ["ada", "open-swe[bot]", "langsmith-engine[bot]"])
async def test_only_auto_merge_requests_merge_when_approved(
    design_only: bool,
    author: str,
    trigger: str,
    auto_merge: bool,
    pending: bool,
    activity: str,
    github_app: AsyncMock,
) -> None:
    from openswe.expedited_review.readiness import Readiness
    from openswe.human_review import standard
    from openswe.human_review.merging import MergeResult
    from openswe.human_review.picking import Coverage

    client = MagicMock()

    async def thread(thread_id: str) -> MagicMock | None:
        if thread_id == "deleted":
            return None
        if thread_id == "review-request":
            if activity == "unavailable":
                raise httpx2.ConnectError("Runtime unavailable")
            if activity in {"busy", "unknown"}:
                return MagicMock(status=activity, runtime="mda")
        return MagicMock(status="idle", runtime="mda")

    async def runs(thread_id: str, *, status: str, limit: int) -> list[dict[str, str]]:
        return [{"status": status}] if activity == status else []

    client.runs.list = AsyncMock(side_effect=runs)
    pr = PullRequest(owner="lc", repo="repo", number=7, author=author)
    pr.threads = [
        ThreadLink(thread_id="implementation", role="primary"),
        ThreadLink(thread_id="deleted"),
    ]
    request = HumanReviewRequest(
        pull_request_id=pr.id, head_sha="abc", kind="standard", auto_merge=auto_merge
    )
    request.pull_request = pr
    request.thread_id = "review-request"
    request.created_at = datetime.now(UTC) - timedelta(hours=3)
    if design_only:
        request.run_config = {"supplementary_reviews": {"design": {"github_login": "grace"}}}
    if pending:
        user = User(
            identities=[UserIdentity(provider="github", external_id="linus", login="linus")]
        )
        reviewer = HumanReviewParticipant(user_id=user.id, decision="review")
        reviewer.user = user
        request.participants.append(reviewer)

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        yield None, request

    with (
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch.object(HumanReviewRequest, "active_for", AsyncMock(return_value=request)),
        patch.object(HumanReviewRequest, "locked", locked),
        patch.object(
            Readiness,
            "assess",
            AsyncMock(return_value=Readiness(snapshot=_snapshot(author=author), blockers=[])),
        ),
        patch.object(
            User, "for_login", AsyncMock(return_value=User() if author == "ada" else None)
        ),
        patch.object(GitHubClient, "as_app", _review_github_app),
        patch.object(
            RepoClient,
            "read_file",
            AsyncMock(return_value='{"reviewAllowedAuthors": ["langsmith-engine[bot]"]}'),
        ),
        patch.object(
            standard, "latest_review_states", AsyncMock(return_value={"grace": "APPROVED"})
        ),
        patch.object(Coverage, "load", AsyncMock(return_value=None)),
        patch.object(ReviewPicks, "complete", AsyncMock()),
        patch.object(ReviewPicks, "release", AsyncMock(return_value=request)),
        patch.object(ReviewCard, "update_blocked_reactions", AsyncMock()),
        patch.object(ReviewCard, "mark_merged", AsyncMock()),
        patch.object(ReviewCard, "refresh", AsyncMock()),
        patch.object(standard.index, "get", AsyncMock(side_effect=thread)),
        patch.object(standard.RUNTIMES, "runs_on", return_value=MagicMock(client=client)),
        patch.object(standard, "_schedule", AsyncMock(return_value=True)) as schedule,
        patch.object(
            standard, "merge_pull_request", AsyncMock(return_value=MergeResult("merged", "Merged"))
        ) as merge,
    ):
        if trigger == "deadline":
            await standard.run_deadline(str(request.id), "auto_merge")
        else:
            await standard.settle_pull_request("lc", "repo", 7)
        held = auto_merge and activity != "idle"
        assert merge.await_count == int(auto_merge and not held and not design_only)
        if held:
            assert request.state == "open"
            assert request.detail.startswith("auto-merge paused")
            schedule.assert_awaited_once_with(request, "auto_merge", timedelta(minutes=5))
            activity = "idle"
            await standard.run_deadline(str(request.id), "auto_merge")
            assert merge.await_count == 1
    if design_only:
        assert request.state == "open"
        assert request.detail == "an approval on GitHub"
    elif not auto_merge:
        assert request.state == "open"
        assert request.detail == (
            "approval from @linus" if pending else "approved — awaiting manual merge"
        )


@pytest.mark.parametrize("channel", ["", "#current", "#requested"])
async def test_existing_request_checks_explicit_channel_before_updating_summary(
    channel: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openswe.github.pull_request_status import PullRequestClient
    from openswe.human_review import standard
    from openswe.slack.channels import SlackChannel
    from openswe.slack.client import GitHubPrRef

    request = HumanReviewRequest(
        pull_request_id=User().id,
        head_sha="abc",
        kind="standard",
        thread_id="owner",
        slack_channel_id="C_CURRENT",
        tldr="Original summary",
    )
    monkeypatch.setattr(HumanReviewRequest, "active_for", AsyncMock(return_value=request))
    monkeypatch.setattr(HumanReviewRequest, "get", AsyncMock(return_value=request))
    monkeypatch.setattr(
        SlackChannel,
        "resolve",
        AsyncMock(
            return_value=SlackChannel(id="C_NEW" if channel == "#requested" else "C_CURRENT")
        ),
    )
    monkeypatch.setattr(standard, "_permalink", AsyncMock(return_value="https://slack/card"))
    monkeypatch.setattr(ReviewCard, "refresh", AsyncMock())

    @asynccontextmanager
    async def locked(*_: object) -> AsyncIterator[tuple[None, HumanReviewRequest]]:
        yield None, request

    monkeypatch.setattr(HumanReviewRequest, "locked", locked)
    result = await standard._request_review(
        GitHubPrRef(owner="lc", repo="repo", number=7, url="https://github.com/lc/repo/pull/7"),
        MagicMock(spec=PullRequestClient),
        standard.Origin(thread_id="owner"),
        channel=channel,
        inline_summary="Updated summary",
    )
    mismatch = channel == "#requested"
    assert result.success and result.reused
    assert result.request_id == str(request.id)
    assert result.channel == "C_CURRENT"
    assert result.channel_mismatch is mismatch
    assert result.requested_channel == ("C_NEW" if mismatch else "")
    assert result.summary_updated is not mismatch
    assert request.tldr == ("Original summary" if mismatch else "Updated summary")


@pytest.mark.parametrize("slack_author", [True, False])
async def test_no_suitable_reviewer_fails_and_notifies_author_without_fallback(
    slack_author: bool,
) -> None:
    from openswe.human_review import standard

    author = User(
        identities=[
            UserIdentity(provider="github", external_id="ada", login="ada"),
            *([UserIdentity(provider="slack", external_id="U_ADA")] if slack_author else []),
        ]
    )
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    with (
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch.object(standard, "_review_author_allowed", AsyncMock(return_value=True)),
        patch.object(standard, "choose_reviewer", AsyncMock(return_value=standard.NoCandidate())),
        patch.object(User, "for_login", AsyncMock(return_value=author)),
        patch.object(standard, "send_dm", AsyncMock(return_value=True)) as dm,
        patch.object(standard.PullRequestClient, "as_app") as app,
        patch.object(standard, "_wake_picker", AsyncMock()) as wake,
    ):
        app.return_value.__aenter__.return_value.comment = AsyncMock()
        result = await standard._auto_assign(request, asked=True, trigger=None)
        assert result.status == "failed"
        wake.assert_not_awaited()
        if slack_author:
            assert dm.await_args.args[0] == "U_ADA"
            assert pr.url in dm.await_args.args[1]
            app.assert_not_called()
        else:
            comment = app.return_value.__aenter__.return_value.comment
            assert "@ada Automatic reviewer assignment failed" in comment.await_args.args[0]
            dm.assert_not_awaited()


@pytest.mark.parametrize("covered", [False, True])
async def test_incomplete_or_covered_search_does_not_notify_author(covered: bool) -> None:
    from openswe.human_review import standard

    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    with (
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch.object(standard, "_review_author_allowed", AsyncMock(return_value=True)),
        patch.object(
            standard,
            "choose_reviewer",
            AsyncMock(return_value=standard.Covered() if covered else None),
        ),
        patch.object(standard, "_assignment_failed", AsyncMock()) as notify,
        patch.object(standard, "_schedule", AsyncMock(return_value=True)) as retry,
    ):
        result = await standard._auto_assign(request, asked=True, trigger=None)
    assert result.status == ("claimed" if covered else "waiting")
    notify.assert_not_awaited()
    assert retry.await_count == (0 if covered else 1)
