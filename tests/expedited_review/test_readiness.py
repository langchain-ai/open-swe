from agent.expedited_review.readiness import (
    PullRequestSnapshot,
    Readiness,
    _latest_reviews_by_user,
    readiness_blockers,
)


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


def test_closed_or_conflicting_pull_requests_are_terminal() -> None:
    closed = _snapshot(state="closed")
    conflicting = _snapshot(mergeable=False, mergeable_state="dirty")
    computing = _snapshot(mergeable=None, mergeable_state="unknown")

    assert Readiness(closed, readiness_blockers(closed)).terminal
    assert Readiness(conflicting, readiness_blockers(conflicting)).terminal
    assert not Readiness(computing, readiness_blockers(computing)).terminal
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
