"""Tool: ``update_finding``. Mutate an existing finding by id."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_findings import (
    get_thread_id_from_runtime,
    update_finding_fields,
)


def update_finding(
    finding_id: str,
    status: str | None = None,
    severity: str | None = None,
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
            Use ``resolved`` when the new commits address the issue.
        severity: New severity, if reassessing.
        description: New description body, if revising.
        suggestion: New replacement text. Pass an empty string to clear it.
        note: Optional free-form note explaining the change. Persisted on the
            finding under ``last_update_note``.

    Returns:
        Dictionary with ``success`` and (on success) the updated ``finding``.
    """
    if status is not None and status not in {"open", "resolved", "dismissed"}:
        return {"success": False, "error": f"Invalid status: {status}"}
    if severity is not None and severity not in {
        "informational",
        "low",
        "medium",
        "high",
        "critical",
    }:
        return {"success": False, "error": f"Invalid severity: {severity}"}

    updates: dict[str, Any] = {}
    if status is not None:
        updates["status"] = status
    if severity is not None:
        updates["severity"] = severity
    if description is not None:
        updates["description"] = description
    if suggestion is not None:
        updates["suggestion"] = suggestion or None
    if note is not None:
        updates["last_update_note"] = note

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    head_sha = configurable.get("head_sha", "") if isinstance(configurable, dict) else ""
    if status == "open" and isinstance(head_sha, str) and head_sha:
        updates["last_confirmed_sha"] = head_sha

    if not updates:
        return {"success": False, "error": "No fields provided to update"}

    thread_id = get_thread_id_from_runtime()
    updated = asyncio.run(update_finding_fields(thread_id, finding_id, updates))
    if updated is None:
        return {"success": False, "error": f"No finding found with id {finding_id}"}
    return {"success": True, "finding": updated}
