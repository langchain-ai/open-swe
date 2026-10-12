import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import httpx2
import pytest

from openswe.expedited_review.readiness import PullRequestSnapshot
from openswe.github.http import GitHubClient, GitHubError, RepoClient
from openswe.github.pull_requests import PullRequest
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
from openswe.slack.blocks import block_payload
from openswe.users import User, UserIdentity

_NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


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


async def test_losing_auto_assignment_does_not_wake_another_picker() -> None:
    from openswe.human_review.picking import Pick
    from openswe.human_review.standard import RequestResult, _auto_assign

    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    with (
        patch.object(HumanReviewRequest, "get", AsyncMock(return_value=request)),
        patch.object(User, "for_login", AsyncMock(return_value=User())),
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


def test_pending_checks_and_unresolved_threads_do_not_block_the_request() -> None:
    assert request_blockers(_snapshot(check_state="pending", unresolved_threads=3)) == []


@pytest.mark.parametrize(
    ("overrides", "blocker"),
    [
        ({"draft": True}, "draft"),
        ({"mergeable": False, "mergeable_state": "dirty"}, "merge conflicts"),
        ({"state": "closed"}, "closed"),
        (
            {"check_state": "failure", "mergeable_state": "blocked", "failing_checks": ["lint"]},
            "required checks are failing: lint",
        ),
    ],
)
def test_requests_are_refused_while_the_pull_request_is_not_reviewable(
    overrides: dict[str, object], blocker: str
) -> None:
    assert any(blocker in reason for reason in request_blockers(_snapshot(**overrides)))


def test_a_failing_check_github_does_not_require_does_not_block_the_request() -> None:
    snapshot = _snapshot(
        check_state="failure",
        mergeable_state="unstable",
        failures_are_required=False,
        failing_checks=["x"],
    )
    assert request_blockers(snapshot) == []


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


def test_one_approval_waits_for_the_other_reviewers_until_the_deadline() -> None:
    states = {"grace": "APPROVED"}
    early = _NOW - timedelta(hours=1)
    late = _NOW - timedelta(hours=2)

    assert merge_wait(["grace", "linus"], early, states, _NOW) == "approval from @linus"
    assert merge_wait(["grace", "linus"], late, states, _NOW) is None


def test_an_approval_from_someone_who_did_not_sign_up_counts() -> None:
    assert merge_wait([], _NOW, {"hopper": "APPROVED"}, _NOW) is None


@pytest.mark.parametrize(
    "states", [{"Grace": "APPROVED", "hopper": "APPROVED", "linus": "DISMISSED"}, None]
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
    with patch(
        "openswe.human_review.lifecycle.latest_review_states", AsyncMock(return_value=states)
    ):
        text, blocks = await ReviewCard(request).render("merged")
    payload = block_payload(blocks)
    assert payload is not None
    rendered = str(payload)
    for message in (text, rendered):
        assert "merged" in message
        assert "by @ada" in message
        if states:
            assert "approved by <@U_grace>, @hopper" in message
        else:
            assert "approved by" not in message
        assert "linus" not in message


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
    ("states", "collapsed"),
    [
        ({"grace": "APPROVED"}, True),
        ({}, False),
        ({"grace": "APPROVED", "linus": "CHANGES_REQUESTED"}, False),
    ],
)
async def test_approved_card_collapses_without_closing_the_request(
    states: dict[str, str], collapsed: bool, github_app: AsyncMock
) -> None:
    pr = PullRequest(owner="o", repo="r", number=1, title="Fix", author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    request.requested_by = None
    with (
        patch(
            "openswe.human_review.lifecycle.latest_review_states", AsyncMock(return_value=states)
        ),
        patch.object(HumanReviewRequest, "author_mention", AsyncMock(return_value="<@U_ada>")),
    ):
        text, blocks = await ReviewCard(request).render(None)
    assert ("Review request: approved" in text) is collapsed
    assert (len(blocks) == 1) is collapsed
    assert "<@U_ada>" in str(block_payload(blocks))
    if collapsed:
        assert "by <@U_ada>" in text
    assert request.state == "open"


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


@pytest.mark.parametrize(("user", "blocked"), [(User(), False), (None, True)])
async def test_review_blockers_refuse_authors_without_an_open_swe_account(
    user: User | None, blocked: bool
) -> None:
    from openswe.expedited_review.readiness import Readiness
    from openswe.human_review.standard import review_blockers

    readiness = Readiness(snapshot=_snapshot(), blockers=[])
    with (
        patch.object(Readiness, "assess", AsyncMock(return_value=readiness)),
        patch.object(User, "for_login", AsyncMock(return_value=user)),
    ):
        blockers = await review_blockers(MagicMock())
    assert blockers == (["its author is not an Open SWE user"] if blocked else [])
