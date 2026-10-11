"""Tool: ``list_review_findings``. Read the published review's findings.

The PR chat agent runs on its own thread; the findings live on the canonical
reviewer thread for the PR. The reviewer thread id is seeded into the run config
by the dashboard chat proxy.
"""

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from openswe import thread_ids
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.review.findings import ReviewerThreadMissingError
from openswe.review.findings import list_findings as list_findings_async
from openswe.run_config import RunConfig
from openswe.slack.client import parse_github_pr_url
from openswe.tools.mcp_exposure import expose_mcp

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


@expose_mcp()
async def list_review_findings(
    status_filter: str | None = None, pr_url: str = ""
) -> dict[str, Any]:
    """Implement the `list_review_findings` tool."""
    if status_filter is not None and status_filter not in {"open", "resolved", "dismissed"}:
        return {"findings": [], "count": 0, "error": f"Invalid status_filter: {status_filter}"}

    cfg = RunConfig.from_runtime()
    reviewer_thread_id = cfg.reviewer_thread_id
    if caller := cfg.mcp_caller:
        pr_ref = parse_github_pr_url(pr_url)
        if pr_ref is None:
            return {"findings": [], "count": 0, "error": "pr_url must be a GitHub PR URL"}
        try:
            await require_repo_access_for_user(caller, f"{pr_ref.owner}/{pr_ref.repo}")
        except HTTPException as exc:
            return {"findings": [], "count": 0, "error": f"Repository access denied: {exc.detail}"}
        reviewer_thread_id = thread_ids.reviewer_thread_id(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if not reviewer_thread_id:
        return {"findings": [], "count": 0, "error": "reviewer thread unavailable"}

    try:
        findings = await list_findings_async(reviewer_thread_id)
    except ReviewerThreadMissingError:
        return {"findings": [], "count": 0, "note": "No review has run on this pull request yet."}
    except Exception as exc:  # noqa: BLE001
        return {"findings": [], "count": 0, "error": f"could not load findings: {exc!s}"}

    if status_filter is not None:
        findings = [f for f in findings if f.get("status") == status_filter]
    compact = [_compact(f) for f in findings]
    return {"findings": compact, "count": len(compact)}
