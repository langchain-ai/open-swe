from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx2
import pytest

from agent.expedited_review.readiness import PullRequestSnapshot
from agent.github.pull_requests import PullRequest
from agent.github.repo_files import RepoSettings
from agent.human_review.lifecycle import _render_standard
from agent.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from agent.human_review.standard import (
    SUMMARY_MAX_CHARS,
    merge_wait,
    request_blockers,
    review_reminder_at,
    summary_line,
)
from agent.slack.blocks import block_payload
from agent.users import User, UserIdentity

_NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


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
async def test_merged_card_names_only_actual_approvers(states: dict[str, str] | None) -> None:
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
    with patch("agent.human_review.lifecycle.review_standings", AsyncMock(return_value=states)):
        text, blocks = await _render_standard(request, "merged", "token")
    payload = block_payload(blocks)
    assert payload is not None
    rendered = str(payload)
    for message in (text, rendered):
        assert "merged" in message
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
    states: dict[str, str], collapsed: bool
) -> None:
    from agent.github.pull_requests import PullRequest
    from agent.human_review.lifecycle import _render_standard
    from agent.human_review.requests import HumanReviewRequest

    pr = PullRequest(owner="o", repo="r", number=1, title="Fix", author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    request.requested_by = None
    with (
        patch("agent.human_review.lifecycle.review_standings", AsyncMock(return_value=states)),
        patch.object(HumanReviewRequest, "author_mention", AsyncMock(return_value="@ada")),
    ):
        text, blocks = await _render_standard(request, None, "token")
    assert ("Review request: approved" in text) is collapsed
    assert (len(blocks) == 1) is collapsed
    assert request.state == "open"


def _github(status: int, text: str = "") -> AsyncMock:
    return AsyncMock(return_value=httpx2.Response(status, text=text))


async def test_repo_settings_prefer_the_pull_request_head() -> None:
    request = _github(200, '{"reviewChannel": "#eng-reviews", "other": 1}')
    with patch("agent.github.repo_files.github_request", request):
        settings = await RepoSettings.fetch("o", "r", token="t", ref="abc123")
    assert settings.review_channel == "#eng-reviews"
    _client, _method, url = request.await_args.args
    assert url.endswith("/repos/o/r/contents/.open-swe/settings.json")
    assert request.await_args.kwargs["params"] == {"ref": "abc123"}


async def test_repo_settings_fall_back_to_the_default_branch() -> None:
    request = AsyncMock(
        side_effect=[
            httpx2.Response(404),
            httpx2.Response(200, text='{"reviewChannel": "#eng-reviews"}'),
        ]
    )
    with patch("agent.github.repo_files.github_request", request):
        settings = await RepoSettings.fetch("o", "r", token="t", ref="abc123")
    assert settings.review_channel == "#eng-reviews"
    assert [call.kwargs["params"] for call in request.await_args_list] == [{"ref": "abc123"}, None]


@pytest.mark.parametrize(("status", "text"), [(404, ""), (200, "not json"), (200, "[]")])
async def test_missing_or_invalid_settings_have_no_review_channel(status: int, text: str) -> None:
    with patch("agent.github.repo_files.github_request", _github(status, text)):
        assert (await RepoSettings.fetch("o", "r", token="t")).review_channel == ""


@pytest.mark.usefixtures("registry_db")
async def test_assignment_inbox_is_personal_and_hides_completed_or_inaccessible_reviews(
    monkeypatch,
):
    from fastapi import HTTPException

    from agent.github.pull_requests import PullRequest
    from agent.human_review.requests import (
        HumanReviewParticipant,
        HumanReviewRequest,
        RequestKind,
        RequestState,
    )
    from agent.human_review.routes import api_review_assignments
    from agent.users import User

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
    async def client(**kwargs):
        yield object()

    async def access(repo, token):
        if repo == "o/hidden":
            raise HTTPException(404, "repository not found")
        return repo

    async def states(client, owner, repo, number, author):
        return {"ada": "APPROVED"} if number == 5 else {}

    with (
        patch("agent.human_review.routes.github_client", client),
        patch(
            "agent.human_review.routes.profiles.get_valid_access_token",
            AsyncMock(return_value="token"),
        ),
        patch("agent.human_review.routes.repo_access.assert_repo_access", access),
        patch("agent.human_review.routes.latest_review_states", states),
    ):
        result = await api_review_assignments(page=1, session={"sub": "ada"})
    assert [row.number for row in result.pull_requests] == [1]
