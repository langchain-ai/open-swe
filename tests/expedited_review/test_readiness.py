from unittest.mock import AsyncMock, patch

import pytest

from agent.expedited_review.readiness import (
    PullRequestSnapshot,
    Readiness,
    _latest_reviews_by_user,
    _resolve_mergeability,
    assess_readiness,
    readiness_blockers,
)
from agent.github.pull_request_status import Mergeability, PullRequestClient


def _snapshot(**overrides: object) -> PullRequestSnapshot:
    base = PullRequestSnapshot(
        state="open",
        merged=False,
        draft=False,
        head_sha="abc123",
        title="Fix typo",
        author="ada",
        mergeable=True,
        mergeable_state="blocked",
        check_state="success",
        unresolved_threads=0,
    )
    for name, value in overrides.items():
        setattr(base, name, value)
    return base


def test_branch_protection_waiting_on_approvals_is_not_a_blocker() -> None:
    assert readiness_blockers(_snapshot(mergeable_state="blocked")) == []


def test_a_required_check_that_has_not_reported_blocks_even_when_the_rest_is_green() -> None:
    blockers = readiness_blockers(_snapshot(unreported_required_checks=["e2e"]))

    assert blockers == ["required checks have not reported yet: e2e"]


def test_a_failing_check_github_does_not_require_is_named_but_does_not_block() -> None:
    advisory = _snapshot(
        check_state="failure",
        mergeable_state="unstable",
        failing_checks=["flaky-e2e"],
        failures_are_required=False,
    )

    assert readiness_blockers(advisory) == []
    assert Readiness(advisory, readiness_blockers(advisory)).ready


def test_a_failing_required_check_blocks_and_names_itself() -> None:
    blockers = readiness_blockers(
        _snapshot(check_state="failure", failing_checks=["unit tests", "lint"])
    )

    assert blockers == ["failing checks: unit tests, lint"]


def test_checks_still_running_block_even_when_nothing_is_required() -> None:
    blockers = readiness_blockers(
        _snapshot(check_state="pending", mergeable_state="unstable", failures_are_required=False)
    )

    assert any("still running" in blocker for blocker in blockers)


def test_every_gate_reports_independently() -> None:
    blockers = readiness_blockers(
        _snapshot(
            draft=True,
            check_state="pending",
            unresolved_threads=2,
            changes_requested_by=["grace"],
        )
    )

    assert len(blockers) == 4
    assert any("draft" in b for b in blockers)
    assert any("still running" in b for b in blockers)
    assert any("2 unresolved review threads" in b for b in blockers)
    assert any("grace" in b for b in blockers)


@pytest.mark.parametrize("live_approval_id", [None, 123])
async def test_assess_readiness_does_not_wait_on_an_open_swe_review(
    live_approval_id: int | None,
) -> None:
    live_reviews = (
        [{"id": live_approval_id, "state": "APPROVED", "user": {"login": "grace"}}]
        if live_approval_id is not None
        else []
    )
    with (
        patch(
            "agent.expedited_review.readiness.fetch_pr",
            AsyncMock(
                return_value={
                    "state": "open",
                    "head": {"sha": "newsha"},
                    "base": {"ref": "main"},
                    "user": {"login": "ada"},
                    "mergeable": True,
                    "mergeable_state": "clean",
                }
            ),
        ),
        patch(
            "agent.expedited_review.readiness.list_check_runs",
            AsyncMock(
                return_value=[
                    {"name": "Open SWE Review", "status": "completed", "conclusion": "neutral"}
                ]
            ),
        ),
        patch("agent.expedited_review.readiness.list_commit_statuses", AsyncMock(return_value=[])),
        patch(
            "agent.expedited_review.readiness.fetch_required_checks", AsyncMock(return_value=set())
        ),
        patch("agent.github.http.github_client"),
        patch.object(PullRequestClient, "unresolved_threads", AsyncMock(return_value=[])),
        patch.object(PullRequestClient, "reviews", AsyncMock(return_value=live_reviews)),
        patch.object(PullRequestClient, "mergeability", AsyncMock(return_value=None)),
    ):
        result = await assess_readiness(owner="lc", repo="repo", pr_number=7, token="t")

    assert result is not None
    assert result.snapshot.check_state == "success"
    assert result.ready
    assert result.snapshot.approved_review_ids == (
        frozenset({live_approval_id}) if live_approval_id is not None else frozenset()
    )


def test_mergeability_still_computing_is_not_ready() -> None:
    computing = _snapshot(mergeable=None, mergeable_state="unknown")

    assert not Readiness(computing, readiness_blockers(computing)).ready


def test_later_approval_clears_a_request_for_changes_but_a_comment_does_not() -> None:
    def review(login: str, state: str) -> dict[str, object]:
        return {"user": {"login": login}, "state": state}

    cleared = _latest_reviews_by_user(
        [review("grace", "CHANGES_REQUESTED"), review("grace", "APPROVED")], author="ada"
    )
    standing = _latest_reviews_by_user(
        [review("grace", "CHANGES_REQUESTED"), review("grace", "COMMENTED")], author="ada"
    )
    own = _latest_reviews_by_user([review("ada", "CHANGES_REQUESTED")], author="ada")

    assert cleared == {"grace": "APPROVED"}
    assert standing == {"grace": "CHANGES_REQUESTED"}
    assert own == {}


def test_graphql_answers_mergeability_that_rest_left_null() -> None:
    stale_rest = {"mergeable": None, "mergeable_state": "unknown"}

    assert _resolve_mergeability(
        stale_rest, Mergeability(mergeable=True, merge_state="blocked")
    ) == (
        True,
        "blocked",
    )
    assert readiness_blockers(_snapshot(mergeable=True, mergeable_state="blocked")) == []


def test_rest_still_decides_when_graphql_is_unavailable_or_unsure() -> None:
    rest = {"mergeable": False, "mergeable_state": "dirty"}
    unsure = Mergeability(mergeable=None, merge_state="unknown")

    assert _resolve_mergeability(rest, None) == (False, "dirty")
    assert _resolve_mergeability(rest, unsure) == (False, "dirty")
