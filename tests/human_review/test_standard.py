from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from agent.expedited_review.readiness import PullRequestSnapshot
from agent.github.repo_files import RepoSettings
from agent.human_review import tldr
from agent.human_review.standard import merge_wait, request_blockers

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
    snapshot = _snapshot(check_state="failure", mergeable_state="unstable", failing_checks=["x"])
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


async def test_a_short_description_is_the_tldr_without_a_model_call() -> None:
    summarize = AsyncMock()
    with patch.object(tldr, "_summarize", summarize):
        text = await tldr.pull_request_tldr("Fix", "<!-- template -->\nFixes the retry loop.\n")
    assert text == "Fixes the retry loop."
    summarize.assert_not_awaited()


async def test_a_long_description_is_summarized_and_falls_back_to_a_clip() -> None:
    body = "word " * 200
    with patch.object(tldr, "_summarize", AsyncMock(return_value="Retries failed uploads.")):
        assert await tldr.pull_request_tldr("Fix", body) == "Retries failed uploads."
    with patch.object(tldr, "_summarize", AsyncMock(side_effect=TimeoutError())):
        clipped = await tldr.pull_request_tldr("Fix", body)
    assert clipped.endswith("…") and len(clipped) <= tldr.TLDR_MAX_CHARS


def _github(status: int, text: str = "") -> AsyncMock:
    return AsyncMock(return_value=httpx2.Response(status, text=text))


async def test_repo_settings_read_the_review_channel_from_the_default_branch() -> None:
    request = _github(200, '{"reviewChannel": "#eng-reviews", "other": 1}')
    with patch("agent.github.repo_files.github_request", request):
        settings = await RepoSettings.fetch("o", "r", token="t")
    assert settings.review_channel == "#eng-reviews"
    _client, _method, url = request.await_args.args
    assert url.endswith("/repos/o/r/contents/.open-swe/settings.json")
    assert request.await_args.kwargs["params"] is None


@pytest.mark.parametrize(("status", "text"), [(404, ""), (200, "not json"), (200, "[]")])
async def test_missing_or_invalid_settings_have_no_review_channel(status: int, text: str) -> None:
    with patch("agent.github.repo_files.github_request", _github(status, text)):
        assert (await RepoSettings.fetch("o", "r", token="t")).review_channel == ""
