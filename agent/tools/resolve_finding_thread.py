"""Tool: ``resolve_finding_thread``. Close one finding's GitHub review thread."""

from typing import Any

from langgraph.config import get_config

from ..github.token import get_github_token
from ..review.findings import (
    TERMINAL_FINDING_STATUSES,
    ReviewerThreadMissingError,
    get_thread_id_from_runtime,
    thread_missing_tool_result,
)
from ..review.thread_resolution import resolve_finding_on_github
from ..utils.reviewer_outcomes import emit_finding_status_outcome


async def resolve_finding_thread(
    finding_id: str,
    note: str,
    status: str = "dismissed",
) -> dict[str, Any]:
    """Resolve the GitHub review thread for a tracked Open SWE finding.

    Use ``status="resolved"`` when the code now fixes the issue. Use
    ``status="dismissed"`` when analysis shows the original review comment was
    not valid. ``note`` is required and is posted verbatim as the full GitHub reply body.
    """
    if status not in TERMINAL_FINDING_STATUSES:
        return {"success": False, "error": f"Invalid status: {status}"}
    normalized_note = note.strip()
    if not normalized_note:
        return {
            "success": False,
            "error": "Resolving or dismissing a finding requires a note with the message to post.",
        }

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    repo_config = configurable.get("repo") if isinstance(configurable, dict) else None
    pr_number = configurable.get("pr_number") if isinstance(configurable, dict) else None
    if (
        not isinstance(repo_config, dict)
        or not repo_config.get("owner")
        or not repo_config.get("name")
        or not isinstance(pr_number, int)
    ):
        return {"success": False, "error": "Missing repo or PR info in run config"}

    token = get_github_token()
    if not token:
        return {"success": False, "error": "No GitHub token available"}

    try:
        result = await resolve_finding_on_github(
            thread_id=get_thread_id_from_runtime(),
            finding_id=finding_id,
            status=status,
            note=normalized_note,
            owner=str(repo_config["owner"]),
            repo=str(repo_config["name"]),
            pr_number=pr_number,
            token=token,
        )
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)

    if result.get("success") and isinstance(result.get("finding"), dict):
        thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
        await emit_finding_status_outcome(
            result["finding"],
            status,
            configurable=configurable,
            thread_id=thread_id if isinstance(thread_id, str) else None,
        )
    return result
