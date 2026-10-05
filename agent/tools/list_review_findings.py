"""Tool: ``list_review_findings``. Read the published review's findings.

The PR chat agent runs on its own thread; the findings live on the canonical
reviewer thread for the PR. The reviewer thread id is seeded into the run config
by the dashboard chat proxy.
"""

from collections.abc import Mapping
from typing import Any

from agent.review.findings import ReviewerThreadMissingError
from agent.review.findings import list_findings as list_findings_async
from agent.run_config import RunConfig
from agent.tools.errors import ToolError

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
        raise ToolError(
            f"Invalid status_filter: {status_filter}", details={"findings": [], "count": 0}
        )

    reviewer_thread_id = RunConfig.from_runtime().reviewer_thread_id
    if not reviewer_thread_id:
        raise ToolError("reviewer thread unavailable", details={"findings": [], "count": 0})

    try:
        findings = await list_findings_async(reviewer_thread_id)
    except ReviewerThreadMissingError:
        return {"findings": [], "count": 0, "note": "No review has run on this pull request yet."}
    except Exception as exc:  # noqa: BLE001
        raise ToolError(
            f"could not load findings: {exc!s}", details={"findings": [], "count": 0}
        ) from exc

    if status_filter is not None:
        findings = [f for f in findings if f.get("status") == status_filter]
    compact = [_compact(f) for f in findings]
    return {"findings": compact, "count": len(compact)}
