"""Tool: ``set_stackability_review``. Records a stackability artifact."""

from __future__ import annotations

from typing import Any

from langgraph.config import get_config

from ..review.findings import (
    ReviewerThreadMissingError,
    resolve_review_head_sha,
    thread_missing_tool_result,
)
from ..review.stackability import (
    new_stackability_review_record,
    validate_stackability_review,
)
from ..review.stackability import (
    set_stackability_review as persist_stackability_review,
)


async def set_stackability_review(
    verdict: str,
    confidence: str,
    rationale: str,
    proposed_stack: list[dict[str, Any]],
    harness_prompt: str,
    risks_or_human_decisions: list[str],
) -> dict[str, Any]:
    """Validate and record a stackability review without publishing it.

    The current pull request head is resolved by the system so the stored
    artifact can be recognized as stale after a later push.
    """
    review = {
        "verdict": verdict,
        "confidence": confidence,
        "rationale": rationale,
        "proposed_stack": proposed_stack,
        "harness_prompt": harness_prompt,
        "risks_or_human_decisions": risks_or_human_decisions,
    }
    errors = validate_stackability_review(review)
    if errors:
        return {
            "success": False,
            "error": "invalid_stackability_review",
            "errors": errors,
        }

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "reviewer_thread_unavailable"}

    try:
        reviewed_head_sha = await resolve_review_head_sha(thread_id, configurable)
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)
    if not reviewed_head_sha:
        return {"success": False, "error": "review_head_unavailable"}

    record = new_stackability_review_record(reviewed_head_sha, review)
    try:
        await persist_stackability_review(thread_id, record)
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)

    return {
        "success": True,
        "verdict": verdict,
        "reviewed_head_sha": reviewed_head_sha,
        "proposed_step_count": len(proposed_stack),
    }
