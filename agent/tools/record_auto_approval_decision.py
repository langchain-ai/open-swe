"""Tool: ``record_auto_approval_decision``. Persist a structured shadow decision."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langgraph.config import get_config

from ..review.findings import (
    ReviewerThreadMissingError,
    get_thread_id_from_runtime,
    resolve_review_head_sha,
    set_reviewer_thread_metadata,
    thread_missing_tool_result,
)

_OUTCOMES = {"AUTO_APPROVE_CANDIDATE", "HUMAN_REVIEW_REQUIRED", "ABSTAIN"}


async def record_auto_approval_decision(
    outcome: str,
    evidence: list[str],
    failed_requirements: list[str],
    rubric_version: str,
) -> dict[str, Any]:
    """Record an explicit, non-publishing auto-approval shadow decision.

    Use this tool only when trusted repository instructions explicitly require
    an auto-approval assessment. A normal review with no findings is not an
    auto-approval decision.

    This tool records reviewer judgment for evaluation and downstream policy.
    It does not approve the pull request, publish a GitHub review, change the
    review Check Run conclusion, or override deterministic eligibility gates.

    Args:
        outcome: One of ``AUTO_APPROVE_CANDIDATE``, ``HUMAN_REVIEW_REQUIRED``,
            or ``ABSTAIN``.
        evidence: Concise facts supporting the outcome. Do not include hidden
            reasoning or unsupported claims.
        failed_requirements: Rubric requirements that failed or could not be
            proven. Use an empty list only when every requirement is satisfied.
        rubric_version: Stable version supplied by the repository rubric.

    Returns:
        The persisted decision, or ``success: false`` with a validation error.
    """
    if outcome not in _OUTCOMES:
        return {"success": False, "error": f"Invalid outcome: {outcome}"}
    if not rubric_version.strip():
        return {"success": False, "error": "rubric_version must be non-empty"}
    if not evidence:
        return {"success": False, "error": "evidence must be non-empty"}
    if outcome == "AUTO_APPROVE_CANDIDATE" and failed_requirements:
        return {
            "success": False,
            "error": "AUTO_APPROVE_CANDIDATE cannot include failed_requirements",
        }

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = get_thread_id_from_runtime()
    try:
        head_sha = await resolve_review_head_sha(thread_id, configurable)
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)

    decision = {
        "outcome": outcome,
        "evidence": evidence,
        "failed_requirements": failed_requirements,
        "rubric_version": rubric_version.strip(),
        "head_sha": head_sha,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    try:
        await set_reviewer_thread_metadata(
            thread_id,
            extra={"auto_approval_decision": decision},
        )
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)
    return {"success": True, "decision": decision}
