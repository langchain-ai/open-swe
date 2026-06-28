from __future__ import annotations

import pytest

from agent.merge_controller import evaluate_auto_merge


def _pr(**overrides: object) -> dict[str, object]:
    pr: dict[str, object] = {
        "number": 7,
        "state": "open",
        "draft": False,
        "head": {"sha": "head-sha"},
    }
    pr.update(overrides)
    return pr


def _check(**overrides: object) -> dict[str, object]:
    check: dict[str, object] = {
        "name": "tests",
        "status": "completed",
        "conclusion": "success",
    }
    check.update(overrides)
    return check


def _decision(**overrides: object):
    kwargs = {
        "pr": _pr(),
        "expected_head_sha": "head-sha",
        "implementation_thread_id": "agent-thread",
        "reviewer_thread_id": "reviewer-thread",
        "reviewed_sha": "head-sha",
        "qa_evidence": {"complete": True},
        "findings": [],
        "required_checks": [_check()],
        "required_check_names": ["tests"],
        "qa_checks": [{"name": "unit tests", "passed": True}],
        "blocking_gates": [{"name": "approval", "passed": True}],
        "merge_policy_enabled": True,
        "credential_available": True,
        "kill_switch": False,
    }
    kwargs.update(overrides)
    return evaluate_auto_merge(**kwargs)


def test_evaluate_auto_merge_allows_passing_path() -> None:
    decision = _decision()

    assert decision.allowed is True
    assert decision.blockers == ()
    assert decision.head_sha == "head-sha"


def test_evaluate_auto_merge_blocks_self_review() -> None:
    decision = _decision(reviewer_thread_id="agent-thread")

    assert decision.allowed is False
    assert "review_threads_not_distinct" in decision.blockers


def test_evaluate_auto_merge_blocks_stale_expected_head_sha() -> None:
    decision = _decision(expected_head_sha="old-sha")

    assert decision.allowed is False
    assert "head_sha_mismatch" in decision.blockers


def test_evaluate_auto_merge_blocks_stale_reviewed_sha() -> None:
    decision = _decision(reviewed_sha="old-sha")

    assert decision.allowed is False
    assert "reviewed_sha_mismatch" in decision.blockers


def test_evaluate_auto_merge_blocks_closed_pr() -> None:
    decision = _decision(pr=_pr(state="closed"))

    assert decision.allowed is False
    assert "pr_not_open" in decision.blockers


def test_evaluate_auto_merge_blocks_draft_pr() -> None:
    decision = _decision(pr=_pr(draft=True))

    assert decision.allowed is False
    assert "pr_is_draft" in decision.blockers


def test_evaluate_auto_merge_blocks_missing_evidence() -> None:
    decision = _decision(qa_evidence={"complete": False})

    assert decision.allowed is False
    assert "qa_evidence_missing" in decision.blockers


def test_evaluate_auto_merge_blocks_unresolved_blocking_finding() -> None:
    decision = _decision(findings=[{"id": "f_block", "status": "open", "blocking": True}])

    assert decision.allowed is False
    assert "unresolved_blocking_finding:f_block" in decision.blockers


@pytest.mark.parametrize(
    "check",
    [
        _check(status="queued", conclusion=None),
        _check(status="completed", conclusion="failure"),
    ],
    ids=["pending", "failing"],
)
def test_evaluate_auto_merge_blocks_pending_or_failing_required_check(
    check: dict[str, object],
) -> None:
    decision = _decision(required_checks=[check])

    assert decision.allowed is False
    assert "required_check_not_passing:tests" in decision.blockers


def test_evaluate_auto_merge_blocks_missing_required_check() -> None:
    decision = _decision(required_checks=[], required_check_names=["tests"])

    assert decision.allowed is False
    assert "required_check_missing:tests" in decision.blockers


def test_evaluate_auto_merge_blocks_failed_configured_qa_check() -> None:
    decision = _decision(qa_checks=[{"name": "unit tests", "passed": False}])

    assert decision.allowed is False
    assert "qa_check_failed:unit tests" in decision.blockers


def test_evaluate_auto_merge_blocks_failed_blocking_gate() -> None:
    decision = _decision(blocking_gates=[{"name": "branch protection", "passed": False}])

    assert decision.allowed is False
    assert "blocking_gate_failed:branch protection" in decision.blockers


def test_evaluate_auto_merge_blocks_policy_denial() -> None:
    decision = _decision(merge_policy_enabled=False)

    assert decision.allowed is False
    assert "merge_policy_disabled" in decision.blockers


def test_evaluate_auto_merge_blocks_missing_credential() -> None:
    decision = _decision(credential_available=False)

    assert decision.allowed is False
    assert "merge_credential_missing" in decision.blockers


def test_evaluate_auto_merge_blocks_kill_switch() -> None:
    decision = _decision(kill_switch=True)

    assert decision.allowed is False
    assert "merge_kill_switch_enabled" in decision.blockers
