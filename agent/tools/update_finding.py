"""Tool: ``update_finding``. Mutate an existing finding by id.

When ``status`` transitions to ``resolved`` or ``dismissed`` and the finding
carries GitHub thread ids (because it was reconstructed from a PR review
thread), the matching GitHub review threads are resolved via the
``resolveReviewThread`` mutation in the same call. The PR is the source of
truth — there is no separate ``resolve_finding_thread`` tool.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.config import get_config

from ..reviewer_findings import (
    MAX_SUGGESTION_LINES,
    Finding,
    clip_suggestion,
    get_finding,
    get_thread_id_from_runtime,
    update_finding_fields,
)
from ..reviewer_publish import resolve_review_thread
from ..utils.github_token import get_github_token

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"resolved", "dismissed"}


def update_finding(
    finding_id: str,
    status: str | None = None,
    severity: str | None = None,
    confidence: str | None = None,
    description: str | None = None,
    suggestion: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Update fields on an existing finding.

    Use this to mark an existing finding as ``resolved`` (the new commits
    address it) or ``dismissed`` (analysis shows the original comment was
    invalid), or to revise its severity/description/suggestion if the new
    commits changed the situation. When ``status`` is ``resolved`` or
    ``dismissed`` and the finding was already published to GitHub, the
    matching PR review thread is also resolved.

    Args:
        finding_id: The id returned by ``add_finding`` (or shown in the
            ``Existing findings`` block of the re-review user message).
        status: New status (``open``, ``resolved``, ``dismissed``). Use
            ``resolved`` when the new commits address the issue; use
            ``dismissed`` when analysis shows the original comment was
            invalid. Both resolve the corresponding GitHub review thread
            for already-published findings.
        severity: New severity, if reassessing.
        confidence: New confidence rating (``low``, ``medium``, ``high``), if
            new commits change how sure you are the finding is a real issue.
        description: New description body, if revising.
        suggestion: New replacement text. Pass an empty string to clear it.
            Capped at 4 lines — longer values are dropped (the finding keeps
            its description). Only set this for small, obvious fixes.
        note: Optional free-form note explaining the change.

    Returns:
        Dictionary with ``success`` and (on success) the updated ``finding``.
        Includes ``resolved_thread_count`` when status moved to a terminal
        value and a GitHub thread was resolved.
    """
    if status is not None and status not in {"open", "resolved", "dismissed"}:
        return {"success": False, "error": f"Invalid status: {status}"}
    if severity is not None and severity not in {"low", "medium", "high", "critical"}:
        return {"success": False, "error": f"Invalid severity: {severity}"}
    if confidence is not None and confidence not in {"low", "medium", "high"}:
        return {"success": False, "error": f"Invalid confidence: {confidence}"}

    updates: dict[str, Any] = {}
    suggestion_dropped = False
    if status is not None:
        updates["status"] = status
    if severity is not None:
        updates["severity"] = severity
    if confidence is not None:
        updates["confidence"] = confidence
    if description is not None:
        updates["description"] = description
    if suggestion is not None:
        if suggestion == "":
            updates["suggestion"] = None
        else:
            clipped, suggestion_dropped = clip_suggestion(suggestion)
            if not suggestion_dropped:
                updates["suggestion"] = clipped

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    head_sha = configurable.get("head_sha", "") if isinstance(configurable, dict) else ""
    if status == "open" and isinstance(head_sha, str) and head_sha:
        updates["last_confirmed_sha"] = head_sha

    if not updates:
        if suggestion_dropped:
            return {
                "success": False,
                "suggestion_dropped": True,
                "error": (
                    f"Suggestion exceeded the {MAX_SUGGESTION_LINES}-line cap "
                    "and was rejected; no other fields were provided, so "
                    "nothing was updated. Only include `suggestion` for "
                    "small, obvious fixes."
                ),
            }
        return {"success": False, "error": "No fields provided to update"}

    return asyncio.run(_update_finding_async(finding_id, updates, note, suggestion_dropped))


async def _update_finding_async(
    finding_id: str,
    updates: dict[str, Any],
    note: str | None,
    suggestion_dropped: bool,
) -> dict[str, Any]:
    thread_id = get_thread_id_from_runtime()
    existing = await get_finding(thread_id, finding_id)
    if existing is None:
        return {"success": False, "error": f"No finding found with id {finding_id}"}

    resolved_count = 0
    should_resolve_threads = (
        updates.get("status") in _TERMINAL_STATUSES
        and existing.get("status") not in _TERMINAL_STATUSES
    )
    if should_resolve_threads:
        resolved_count = await _resolve_github_threads_for_finding(existing)

    updated = await update_finding_fields(thread_id, finding_id, updates)
    if updated is None:
        return {"success": False, "error": f"No finding found with id {finding_id}"}

    result: dict[str, Any] = {"success": True, "finding": updated}
    if resolved_count > 0:
        result["resolved_thread_count"] = resolved_count
    if note:
        result["note"] = note
    if suggestion_dropped:
        result["suggestion_dropped"] = True
        result["warning"] = (
            f"Suggestion exceeded the {MAX_SUGGESTION_LINES}-line cap and was "
            "rejected — the finding's prior `suggestion` was left unchanged "
            "and other fields were updated normally. Only include "
            "`suggestion` for small, obvious fixes."
        )
    return result


async def _resolve_github_threads_for_finding(finding: Finding) -> int:
    thread_ids = [
        thread_id
        for thread_id in finding.get("github_review_thread_ids") or []
        if isinstance(thread_id, str) and thread_id
    ]
    if not thread_ids:
        return 0
    token = get_github_token()
    if not token:
        logger.warning(
            "update_finding: no GitHub token available to resolve thread(s) for finding %s",
            finding.get("id"),
        )
        return 0
    resolved = 0
    for thread_node_id in thread_ids:
        ok = await resolve_review_thread(thread_node_id=thread_node_id, token=token)
        if ok:
            resolved += 1
    return resolved
