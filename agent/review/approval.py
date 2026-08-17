"""Structured approval assessments for Open SWE Review."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict, cast

from ..dashboard.review_approval_policies import EffectiveApprovalPolicy
from .findings import Finding, get_thread_metadata, set_reviewer_thread_metadata

APPROVAL_RUBRIC_VERSION = "1"
APPROVAL_TEXT_LIMIT = 500
APPROVAL_LIST_LIMIT = 5
_BLOCKING_SEVERITIES = frozenset({"medium", "high", "critical"})


class ApprovalAssessment(TypedDict):
    rubric_version: str
    assessed_sha: str
    raw_score: int | None
    score: int | None
    reasons: list[str]
    risks: list[str]
    valid: bool
    policy: EffectiveApprovalPolicy
    decision: str
    blockers: list[str]
    open_blocking_finding_ids: list[str]
    github_review_id: int | None
    github_review_event: str
    recorded_at: str


def _normalize_text_list(value: object, *, required: bool) -> list[str] | None:
    if not isinstance(value, list) or len(value) > APPROVAL_LIST_LIMIT:
        return None
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not text or len(text) > APPROVAL_TEXT_LIMIT:
            return None
        normalized.append(text)
    if required and not normalized:
        return None
    return normalized


def open_blocking_finding_ids(findings: list[Finding]) -> list[str]:
    return [
        finding["id"]
        for finding in findings
        if finding.get("status", "open") == "open"
        and finding.get("severity", "low") in _BLOCKING_SEVERITIES
        and isinstance(finding.get("id"), str)
    ]


def build_approval_assessment(
    *,
    assessed_sha: str,
    raw_score: object,
    reasons: object,
    risks: object,
    findings: list[Finding],
    policy: EffectiveApprovalPolicy,
) -> ApprovalAssessment:
    normalized_reasons = _normalize_text_list(reasons, required=True)
    normalized_risks = _normalize_text_list(risks, required=False)
    validated_score = (
        raw_score
        if isinstance(raw_score, int) and not isinstance(raw_score, bool) and 0 <= raw_score <= 100
        else None
    )
    valid = (
        validated_score is not None
        and normalized_reasons is not None
        and normalized_risks is not None
    )
    blocking_ids = open_blocking_finding_ids(findings)
    effective_score = validated_score
    if effective_score is not None and blocking_ids:
        effective_score = 0

    blockers: list[str] = []
    if not valid:
        blockers.append("invalid_assessment")
    if not policy["team_enabled"]:
        blockers.append("team_auto_approve_disabled")
    if not policy["repo_enabled"]:
        blockers.append("repo_auto_approve_disabled")
    if blocking_ids:
        blockers.append("open_blocking_findings")
    if effective_score is None or effective_score < policy["effective_threshold"]:
        blockers.append("score_below_threshold")

    return {
        "rubric_version": APPROVAL_RUBRIC_VERSION,
        "assessed_sha": assessed_sha,
        "raw_score": validated_score,
        "score": effective_score,
        "reasons": normalized_reasons or [],
        "risks": normalized_risks or [],
        "valid": valid,
        "policy": policy,
        "decision": "commented",
        "blockers": blockers,
        "open_blocking_finding_ids": blocking_ids,
        "github_review_id": None,
        "github_review_event": "COMMENT",
        "recorded_at": datetime.now(UTC).isoformat(),
    }


def approval_is_preliminarily_eligible(assessment: ApprovalAssessment) -> bool:
    return assessment["valid"] and not assessment["blockers"]


async def persist_approval_assessment(thread_id: str, assessment: ApprovalAssessment) -> None:
    metadata = await get_thread_metadata(thread_id)
    raw_assessments = metadata.get("approval_assessments")
    assessments = dict(raw_assessments) if isinstance(raw_assessments, dict) else {}
    assessments[assessment["assessed_sha"]] = assessment
    await set_reviewer_thread_metadata(
        thread_id,
        extra={
            "approval_assessments": assessments,
            "latest_approval_assessment_sha": assessment["assessed_sha"],
        },
    )


def latest_approval_assessment(metadata: dict[str, Any]) -> ApprovalAssessment | None:
    latest_sha = metadata.get("latest_approval_assessment_sha")
    assessments = metadata.get("approval_assessments")
    if not isinstance(latest_sha, str) or not isinstance(assessments, dict):
        return None
    value = assessments.get(latest_sha)
    return cast(ApprovalAssessment, value) if isinstance(value, dict) else None
