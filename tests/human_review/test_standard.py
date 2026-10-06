from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import httpx2
import pytest
from githubkit import GitHub

from agent.expedited_review.readiness import PullRequestSnapshot
from agent.github.pull_requests import PullRequest
from agent.github.repo_files import RepoSettings
from agent.human_review.lifecycle import _render_standard, _unrequest_github_review
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


@pytest.mark.parametrize("status", [200, 503])
async def test_reviewer_removal_sends_delete_body_without_aborting(status: int) -> None:
    pr = PullRequest(owner="lc", repo="repo", number=7, author="ada")
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    received: list[httpx.Request] = []

    def respond(outgoing: httpx.Request) -> httpx.Response:
        received.append(outgoing)
        return httpx.Response(status, json={})

    client = GitHub("token", async_transport=httpx.MockTransport(respond), auto_retry=False)
    with patch("agent.human_review.lifecycle.github_sdk", return_value=client):
        await _unrequest_github_review(request, "grace", "token")
    assert len(received) == 1
    assert received[0].method == "DELETE"
    assert received[0].url.path == "/repos/lc/repo/pulls/7/requested_reviewers"
    assert received[0].content == b'{"reviewers":["grace"]}'


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
    from agent.human_review.standard import AutoAssignResult, run_deadline

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
        patch("agent.human_review.standard._assignment_minutes", AsyncMock(return_value=minutes)),
        patch("agent.human_review.standard._schedule", AsyncMock(return_value=True)),
        patch("agent.human_review.standard._github_approvers", AsyncMock(return_value=[])),
        patch(
            "agent.human_review.standard.start_auto_assign",
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
    with patch("agent.human_review.lifecycle.latest_review_states", AsyncMock(return_value=states)):
        text, blocks = await _render_standard(request, "merged", "token")
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
        patch("agent.human_review.lifecycle.latest_review_states", AsyncMock(return_value=states)),
        patch.object(HumanReviewRequest, "author_mention", AsyncMock(return_value="<@U_ada>")),
    ):
        text, blocks = await _render_standard(request, None, "token")
    assert ("Review request: approved" in text) is collapsed
    assert (len(blocks) == 1) is collapsed
    assert "<@U_ada>" in str(block_payload(blocks))
    if collapsed:
        assert "by <@U_ada>" in text
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
