from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from agent.expedited_review.readiness import PullRequestSnapshot
from agent.github.pull_requests import PullRequest
from agent.github.repo_files import RepoSettings
from agent.human_review.lifecycle import _render_standard
from agent.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from agent.human_review.standard import (
    SUMMARY_MAX_CHARS,
    Origin,
    RequestResult,
    _target_channel,
    merge_wait,
    request_blockers,
    summary_line,
)
from agent.slack.blocks import block_payload
from agent.slack.channels import SlackChannel
from agent.slack.client import GitHubPrRef
from agent.users import User, UserIdentity
from agent.workspaces.store import WORKSPACES, Workspace

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
    with patch("agent.human_review.lifecycle.latest_review_states", AsyncMock(return_value=states)):
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
        patch("agent.human_review.lifecycle.latest_review_states", AsyncMock(return_value=states)),
        patch.object(HumanReviewRequest, "author_mention", AsyncMock(return_value="@ada")),
    ):
        text, blocks = await _render_standard(request, None, "token")
    assert ("Review request: approved" in text) is collapsed
    assert (len(blocks) == 1) is collapsed
    assert request.state == "open"


@pytest.mark.parametrize(
    ("override", "workspace", "source_channel", "workspace_channel", "available", "expected"),
    [
        (" CEXPLICIT ", "thread", "CSOURCE", "CTHREAD", True, "CEXPLICIT"),
        ("", "thread", "CSOURCE", "CTHREAD", True, "CTHREAD"),
        ("", None, "CSOURCE", "CTHREAD", True, "CSLACK"),
        ("", None, "", "CTHREAD", True, "CREPOWORKSPACE"),
        ("", "thread", "CSOURCE", None, True, "CREPOCONFIG"),
        ("", "thread", "CSOURCE", "CTHREAD", False, "CTHREAD"),
    ],
)
async def test_review_channel_precedence(
    override: str,
    workspace: str | None,
    source_channel: str,
    workspace_channel: str | None,
    available: bool,
    expected: str,
) -> None:
    definitions = {
        "thread": Workspace(slug="thread", review_channel_id=workspace_channel),
        "slack": Workspace(slug="slack", review_channel_id="CSLACK"),
        "repo": Workspace(slug="repo", review_channel_id="CREPOWORKSPACE"),
    }

    async def channel_for(reference: str) -> SlackChannel | None:
        return SlackChannel(id=reference) if available else None

    with (
        patch.object(WORKSPACES, "get", AsyncMock(side_effect=definitions.get)),
        patch.object(WORKSPACES, "owner_of_slack_channel", AsyncMock(return_value="slack")),
        patch.object(WORKSPACES, "owner_of_repo", AsyncMock(return_value="repo")),
        patch.object(
            RepoSettings,
            "fetch",
            AsyncMock(return_value=RepoSettings(review_channel="CREPOCONFIG")),
        ),
        patch.object(SlackChannel, "resolve", channel_for),
    ):
        target = await _target_channel(
            GitHubPrRef(owner="o", repo="r", number=1, url="https://github.com/o/r/pull/1"),
            override,
            "token",
            "head",
            Origin(workspace=workspace, slack_channel_id=source_channel),
        )
    if available:
        assert isinstance(target, SlackChannel)
        assert target.id == expected
    else:
        assert isinstance(target, RequestResult)
        assert not target.success
        assert expected in target.error


def _github(status: int, text: str = "") -> AsyncMock:
    return AsyncMock(return_value=httpx2.Response(status, text=text))


async def test_repo_settings_prefer_the_pull_request_head() -> None:
    request = _github(200, '{"reviewChannel": "#eng-reviews", "other": 1}')
    with patch("agent.github.repo_files.github_request", request):
        settings = await RepoSettings.fetch("o", "r", token="t", ref="abc123")
    assert settings.review_channel == "#eng-reviews"
    assert request.await_args is not None
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
