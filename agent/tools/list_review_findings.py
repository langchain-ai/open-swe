"""Tool: ``list_review_findings``. Read the published review's findings.

The PR chat agent runs on its own thread; the findings live on the canonical
reviewer thread for the PR. The reviewer thread id is seeded into the run config
by the dashboard chat proxy.
"""

from collections.abc import Mapping
from typing import Any

from agent.review.findings import list_findings as list_findings_async
from agent.run_config import RunConfig

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


def _compact(finding: Mapping[str, Any]) -> dict[str, Any]:
    return {key: finding.get(key) for key in _COMPACT_FIELDS if finding.get(key) is not None}


async def list_review_findings(status_filter: str | None = None) -> dict[str, Any]:
    """Implement the `list_review_findings` tool."""
    if status_filter is not None and status_filter not in {"open", "resolved", "dismissed"}:
        return {"findings": [], "count": 0, "error": f"Invalid status_filter: {status_filter}"}

    reviewer_thread_id = RunConfig.from_runtime().reviewer_thread_id
    if not reviewer_thread_id:
        return {"findings": [], "count": 0, "error": "reviewer thread unavailable"}

    try:
        findings = await list_findings_async(reviewer_thread_id)
    except Exception as exc:  # noqa: BLE001
        return {"findings": [], "count": 0, "error": f"could not load findings: {exc!s}"}

    if status_filter is not None:
        findings = [f for f in findings if f.get("status") == status_filter]
    compact = [_compact(f) for f in findings]
    return {"findings": compact, "count": len(compact)}
