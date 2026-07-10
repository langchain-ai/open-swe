"""Tool: ``list_findings``. Return findings persisted on the reviewer thread."""

from __future__ import annotations

from typing import Any

from ..review.findings import (
    ReviewerThreadMissingError,
    get_thread_id_from_runtime,
    thread_missing_tool_result,
)
from ..review.findings import (
    list_findings as list_findings_async,
)


async def list_findings(status_filter: str | None = None) -> dict[str, Any]:
    """List findings on the reviewer thread, optionally filtered by status.

    Most useful on a re-review run to inspect what existed before deciding
    which findings the new commits resolved.

    Args:
        status_filter: One of ``open``, ``resolved``, ``dismissed``. ``None``
            (default) returns every finding regardless of status.

    Returns:
        Dictionary with ``findings`` (list) and ``count`` (int).
    """
    if status_filter is not None and status_filter not in {"open", "resolved", "dismissed"}:
        return {"findings": [], "count": 0, "error": f"Invalid status_filter: {status_filter}"}

    thread_id = get_thread_id_from_runtime()
    try:
        findings = await list_findings_async(thread_id)
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)
    if status_filter is not None:
        findings = [f for f in findings if f.get("status") == status_filter]
    return {"findings": findings, "count": len(findings)}
