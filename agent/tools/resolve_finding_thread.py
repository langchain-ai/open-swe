from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_findings import get_finding, get_thread_id_from_runtime, update_finding_fields
from ..reviewer_publish import fetch_review_thread_id_for_comment, resolve_review_thread
from ..utils.github_token import get_github_token


def resolve_finding_thread(
    finding_id: str,
    status: str = "dismissed",
    note: str | None = None,
) -> dict[str, Any]:
    """Resolve the GitHub review thread for a tracked Open SWE finding.

    Use ``status="resolved"`` when the code now fixes the issue. Use
    ``status="dismissed"`` when analysis shows the original review comment was
    not valid.
    """
    if status not in {"resolved", "dismissed"}:
        return {"success": False, "error": f"Invalid status: {status}"}

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

    return asyncio.run(
        _resolve_finding_thread_async(
            finding_id=finding_id,
            status=status,
            note=note,
            owner=str(repo_config["owner"]),
            repo=str(repo_config["name"]),
            pr_number=pr_number,
            token=token,
        )
    )


async def _resolve_finding_thread_async(
    *,
    finding_id: str,
    status: str,
    note: str | None,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> dict[str, Any]:
    thread_id = get_thread_id_from_runtime()
    finding = await get_finding(thread_id, finding_id)
    if finding is None:
        return {"success": False, "error": f"No finding found with id {finding_id}"}

    github_thread_id = finding.get("github_review_thread_id")
    if not isinstance(github_thread_id, str) or not github_thread_id:
        comment_id = finding.get("github_review_comment_id")
        if not isinstance(comment_id, int):
            return {"success": False, "error": "Finding has no GitHub review thread mapping"}
        github_thread_id = await fetch_review_thread_id_for_comment(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_comment_id=comment_id,
            token=token,
        )
    if not github_thread_id:
        return {"success": False, "error": "Could not resolve GitHub review thread id"}

    ok = await resolve_review_thread(thread_node_id=github_thread_id, token=token)
    if not ok:
        return {"success": False, "error": "GitHub did not resolve the review thread"}

    updates: dict[str, Any] = {
        "status": status,
        "github_review_thread_id": github_thread_id,
        "github_thread_resolved": True,
    }
    if note:
        updates["last_reconciliation_note"] = note
    updated = await update_finding_fields(thread_id, finding_id, updates)
    return {"success": True, "finding": updated}
