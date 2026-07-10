"""Tool: ``list_review_findings``. Read the published review's findings.

The PR chat agent runs on its own thread; the findings live on the canonical
reviewer thread for the PR. The reviewer thread id is seeded into the run config
by the dashboard chat proxy.
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_config

from ..review.findings import list_findings as list_findings_async

_COMPACT_FIELDS = (
    "id",
    "severity",
    "confidence",
    "category",
    "title",
    "description",
    "suggestion",
    "file",
    "start_line",
    "end_line",
    "side",
    "status",
    "resolution_note",
)


def _compact(finding: dict[str, Any]) -> dict[str, Any]:
    return {key: finding.get(key) for key in _COMPACT_FIELDS if finding.get(key) is not None}


async def list_review_findings(status_filter: str | None = None) -> dict[str, Any]:
    """List the findings the reviewer published for this PR.

    Use this to ground answers about the review — what was flagged, the
    severity/confidence, and any resolution notes. Prefer quoting these over
    re-deriving issues from the diff.

    Args:
        status_filter: One of ``open``, ``resolved``, ``dismissed``. ``None``
            (default) returns findings of every status.

    Returns:
        ``{findings, count}``; ``{findings: [], count: 0, error}`` on failure.
    """
    if status_filter is not None and status_filter not in {"open", "resolved", "dismissed"}:
        return {"findings": [], "count": 0, "error": f"Invalid status_filter: {status_filter}"}

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    reviewer_thread_id = (
        configurable.get("reviewer_thread_id") if isinstance(configurable, dict) else None
    )
    if not isinstance(reviewer_thread_id, str) or not reviewer_thread_id:
        return {"findings": [], "count": 0, "error": "reviewer thread unavailable"}

    try:
        findings = await list_findings_async(reviewer_thread_id)
    except Exception as exc:  # noqa: BLE001
        return {"findings": [], "count": 0, "error": f"could not load findings: {exc!s}"}

    if status_filter is not None:
        findings = [f for f in findings if f.get("status") == status_filter]
    compact = [_compact(f) for f in findings]
    return {"findings": compact, "count": len(compact)}
