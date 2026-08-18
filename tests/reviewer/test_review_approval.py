from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.review_api import _serialize_approval_assessment
from agent.review.approval import build_approval_assessment, persist_approval_assessment
from agent.review.findings import new_finding
from agent.reviewer import REVIEWER_PROMPT_TEMPLATE


def _policy(**overrides: Any) -> dict[str, Any]:
    return {
        "team_enabled": True,
        "team_threshold": 90,
        "repo_enabled": True,
        "repo_threshold": None,
        "effective_enabled": True,
        "effective_threshold": 90,
        **overrides,
    }


def _finding(*, severity: str = "medium", status: str = "open"):
    finding = new_finding(
        severity=severity,  # type: ignore[arg-type]
        confidence="high",
        category="correctness",
        file="x.py",
        start_line=1,
        end_line=1,
        description="Issue",
        sha="abc",
    )
    finding["status"] = status  # type: ignore[typeddict-item]
    return finding


def test_reviewer_prompt_requires_structured_approval_assessment() -> None:
    assert "Every call must include `approval_score`" in REVIEWER_PROMPT_TEMPLATE
    assert "backend owns policy" in REVIEWER_PROMPT_TEMPLATE


def test_build_approval_assessment_accepts_score_bounds() -> None:
    for score in (0, 100):
        assessment = build_approval_assessment(
            assessed_sha="abc",
            raw_score=score,
            reasons=["Reviewed the complete diff"],
            risks=[],
            findings=[],
            policy=_policy(),  # type: ignore[arg-type]
        )
        assert assessment["valid"] is True
        assert assessment["raw_score"] == score


def test_build_approval_assessment_rejects_invalid_scores_and_reasons() -> None:
    for score in (-1, 101, True, "95"):
        assessment = build_approval_assessment(
            assessed_sha="abc",
            raw_score=score,
            reasons=["Reviewed the complete diff"],
            risks=[],
            findings=[],
            policy=_policy(),  # type: ignore[arg-type]
        )
        assert assessment["valid"] is False
        assert "invalid_assessment" in assessment["blockers"]

    missing_reasons = build_approval_assessment(
        assessed_sha="abc",
        raw_score=95,
        reasons=[],
        risks=[],
        findings=[],
        policy=_policy(),  # type: ignore[arg-type]
    )
    assert missing_reasons["valid"] is False


def test_open_medium_or_higher_finding_caps_score_and_blocks() -> None:
    for severity in ("medium", "high", "critical"):
        assessment = build_approval_assessment(
            assessed_sha="abc",
            raw_score=99,
            reasons=["Reviewed"],
            risks=[],
            findings=[_finding(severity=severity)],
            policy=_policy(),  # type: ignore[arg-type]
        )
        assert assessment["score"] == 0
        assert "open_blocking_findings" in assessment["blockers"]


def test_low_or_closed_finding_does_not_cap_score() -> None:
    assessment = build_approval_assessment(
        assessed_sha="abc",
        raw_score=95,
        reasons=["Reviewed"],
        risks=[],
        findings=[_finding(severity="low"), _finding(severity="high", status="resolved")],
        policy=_policy(),  # type: ignore[arg-type]
    )
    assert assessment["score"] == 95
    assert "open_blocking_findings" not in assessment["blockers"]


def test_disabled_policy_and_threshold_are_blockers() -> None:
    assessment = build_approval_assessment(
        assessed_sha="abc",
        raw_score=89,
        reasons=["Reviewed"],
        risks=[],
        findings=[],
        policy=_policy(team_enabled=False, repo_enabled=False),  # type: ignore[arg-type]
    )
    assert assessment["blockers"] == [
        "team_auto_approve_disabled",
        "repo_auto_approve_disabled",
        "score_below_threshold",
    ]


def test_review_api_serializes_and_marks_stale_assessment() -> None:
    assessment = build_approval_assessment(
        assessed_sha="old",
        raw_score=95,
        reasons=["Reviewed"],
        risks=[],
        findings=[],
        policy=_policy(),  # type: ignore[arg-type]
    )
    metadata = {
        "latest_approval_assessment_sha": "old",
        "approval_assessments": {"old": assessment},
    }

    serialized = _serialize_approval_assessment(metadata, "new")

    assert serialized is not None
    assert serialized["score"] == 95
    assert serialized["stale"] is True


@pytest.mark.asyncio
async def test_persist_assessment_replaces_same_sha_and_preserves_history() -> None:
    assessment = build_approval_assessment(
        assessed_sha="head",
        raw_score=95,
        reasons=["Reviewed"],
        risks=[],
        findings=[],
        policy=_policy(),  # type: ignore[arg-type]
    )
    set_metadata = AsyncMock()
    with (
        patch(
            "agent.review.approval.get_thread_metadata",
            AsyncMock(return_value={"approval_assessments": {"old": {"score": 80}}}),
        ),
        patch("agent.review.approval.set_reviewer_thread_metadata", set_metadata),
    ):
        await persist_approval_assessment("tid", assessment)

    assert set_metadata.await_args is not None
    stored = set_metadata.await_args.kwargs["extra"]["approval_assessments"]
    assert set(stored) == {"old", "head"}
    assert stored["head"]["score"] == 95
