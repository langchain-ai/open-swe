"""Tool: ``update_finding``. Mutate an existing finding by id."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_findings import (
    DEFAULT_FINDING_TITLE,
    MAX_SUGGESTION_LINES,
    Finding,
    clip_suggestion,
    get_thread_id_from_runtime,
    list_findings,
    normalize_finding_title,
    resolve_review_head_sha,
    update_finding_fields,
)
from ..utils.reviewer_outcomes import emit_finding_status_outcome


def _is_non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _normalize_note(note: str | None) -> str | None:
    if note is None:
        return None
    normalized = note.strip()
    return normalized or None


def _has_published_github_surface(finding: Finding) -> bool:
    surface = finding.get("surface")
    if isinstance(surface, dict) and (
        isinstance(surface.get("github_review_comment_id"), int)
        or _is_non_empty_str(surface.get("github_review_thread_id"))
    ):
        return True
    comment_ids = finding.get("github_review_comment_ids")
    thread_ids = finding.get("github_review_thread_ids")
    return (
        isinstance(finding.get("github_review_comment_id"), int)
        or _is_non_empty_str(finding.get("github_review_thread_id"))
        or (isinstance(comment_ids, list) and any(isinstance(item, int) for item in comment_ids))
        or (isinstance(thread_ids, list) and any(_is_non_empty_str(item) for item in thread_ids))
    )


def update_finding(
    finding_id: str,
    status: str | None = None,
    severity: str | None = None,
    confidence: str | None = None,
    title: str | None = None,
    description: str | None = None,
    suggestion: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Update fields on an existing finding.

    Use this on a re-review run to mark an existing finding as resolved or
    dismissed, or to revise its severity/description/suggestion if the new
    commits changed the situation.

    Args:
        finding_id: The id returned by ``add_finding`` (or shown in the
            ``Existing findings`` block of the re-review user message).
        status: New status (``open``, ``resolved``, ``dismissed``).
            Use ``resolved`` when the new commits address the issue. Resolving
            or dismissing requires a ``note`` with the message to post.
        severity: New severity, if reassessing.
        confidence: New confidence rating (``low``, ``medium``, ``high``), if
            new commits change how sure you are the finding is a real issue.
        title: New concise generated headline, if revising.
        description: New description body, if revising. Do not repeat ``title``
            as the first line.
        suggestion: New replacement text. Pass an empty string to clear it.
            Capped at 4 lines — longer values are dropped (the finding keeps
            its description). Only set this for small, obvious fixes.
        note: Optional free-form note explaining the change. Required when
            resolving or dismissing because it becomes the GitHub reply body.

    Returns:
        Dictionary with ``success`` and (on success) the updated ``finding``.
    """
    if status is not None and status not in {"open", "resolved", "dismissed"}:
        return {"success": False, "error": f"Invalid status: {status}"}
    if severity is not None and severity not in {"low", "medium", "high", "critical"}:
        return {"success": False, "error": f"Invalid severity: {severity}"}
    if confidence is not None and confidence not in {"low", "medium", "high"}:
        return {"success": False, "error": f"Invalid confidence: {confidence}"}
    normalized_note = _normalize_note(note)
    if status in {"resolved", "dismissed"} and normalized_note is None:
        return {
            "success": False,
            "error": "Resolving or dismissing a finding requires a note with the message to post.",
        }

    updates: dict[str, Any] = {}
    suggestion_dropped = False
    if status is not None:
        updates["status"] = status
    if severity is not None:
        updates["severity"] = severity
    if confidence is not None:
        updates["confidence"] = confidence
    if title is not None:
        normalized_title = normalize_finding_title(title)
        if normalized_title == DEFAULT_FINDING_TITLE:
            return {"success": False, "error": "title must be a non-empty generated headline"}
        updates["title"] = normalized_title
    if description is not None:
        updates["description"] = description
    if suggestion is not None:
        if suggestion == "":
            updates["suggestion"] = None
        else:
            clipped, suggestion_dropped = clip_suggestion(suggestion)
            if not suggestion_dropped:
                updates["suggestion"] = clipped
    if normalized_note is not None:
        updates["last_update_note"] = normalized_note
        if status in {"resolved", "dismissed"}:
            updates["resolution_note"] = normalized_note

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    if status == "open":
        head_sha = asyncio.run(resolve_review_head_sha(get_thread_id_from_runtime(), configurable))
        if head_sha:
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

    thread_id = get_thread_id_from_runtime()
    findings = asyncio.run(list_findings(thread_id))
    finding = next((item for item in findings if item.get("id") == finding_id), None)
    if finding is None:
        return {"success": False, "error": f"No finding found with id {finding_id}"}

    delegated_resolution = False
    repo_config = configurable.get("repo") if isinstance(configurable, dict) else None
    pr_number = configurable.get("pr_number") if isinstance(configurable, dict) else None
    can_resolve_github_thread = (
        isinstance(repo_config, dict)
        and bool(repo_config.get("owner"))
        and bool(repo_config.get("name"))
        and isinstance(pr_number, int)
    )
    if (
        status in {"resolved", "dismissed"}
        and can_resolve_github_thread
        and _has_published_github_surface(finding)
    ):
        from .resolve_finding_thread import resolve_finding_thread

        resolve_result = resolve_finding_thread(finding_id, status=status, note=normalized_note)
        if not resolve_result.get("success"):
            return {
                "success": False,
                "error": "GitHub review thread resolution failed; finding was left open.",
                "github_resolution": resolve_result,
            }
        delegated_resolution = True
        updates.pop("status", None)
        updates.pop("last_update_note", None)
        updates.pop("resolution_note", None)
        if not updates:
            result: dict[str, Any] = {
                "success": True,
                "finding": resolve_result.get("finding"),
                "github_resolution": resolve_result,
            }
            if suggestion_dropped:
                result["suggestion_dropped"] = True
                result["warning"] = (
                    f"Suggestion exceeded the {MAX_SUGGESTION_LINES}-line cap and was "
                    "rejected — the finding's prior `suggestion` was left unchanged. "
                    "Only include `suggestion` for small, obvious fixes."
                )
            return result

    updated = asyncio.run(update_finding_fields(thread_id, finding_id, updates))
    if updated is None:
        return {"success": False, "error": f"No finding found with id {finding_id}"}
    if status in {"resolved", "dismissed"} and not delegated_resolution:
        emit_finding_status_outcome(updated, status, configurable=configurable, thread_id=thread_id)
    result = {"success": True, "finding": updated}
    if suggestion_dropped:
        result["suggestion_dropped"] = True
        result["warning"] = (
            f"Suggestion exceeded the {MAX_SUGGESTION_LINES}-line cap and was "
            "rejected — the finding's prior `suggestion` was left unchanged "
            "and other fields were updated normally. Only include "
            "`suggestion` for small, obvious fixes."
        )
    return result
