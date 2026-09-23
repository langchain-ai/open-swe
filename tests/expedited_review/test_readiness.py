from agent.expedited_review.readiness import (
    PullRequestSnapshot,
    Readiness,
    _latest_reviews_by_user,
    _resolve_mergeability,
    readiness_blockers,
)
from agent.github.pull_request_status import Mergeability


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
            open_swe_review_required=True,
        )
    )

    assert len(blockers) == 5
    assert any("draft" in b for b in blockers)
    assert any("still running" in b for b in blockers)
    assert any("2 unresolved review threads" in b for b in blockers)
    assert any("grace" in b for b in blockers)
    assert any("Open SWE" in b for b in blockers)


def test_open_swe_review_only_required_where_enabled() -> None:
    assert readiness_blockers(_snapshot(open_swe_review_required=False)) == []
    assert (
        readiness_blockers(_snapshot(open_swe_review_required=True, open_swe_reviewed_head=True))
        == []
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
