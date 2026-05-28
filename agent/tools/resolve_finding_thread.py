from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_findings import (
    Finding,
    get_finding,
    get_thread_id_from_runtime,
    update_finding_fields,
    update_finding_surface,
)
from ..reviewer_publish import (
    fetch_pr_review_threads,
    fetch_review_thread_id_for_comment,
    render_resolution_comment,
    reply_to_review_comment,
    resolve_review_thread,
)
from ..reviewer_reconcile import reconcile_findings_with_review_threads
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
    finding = await _get_finding_with_pr_backfill(
        thread_id=thread_id,
        finding_id=finding_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
    )
    if finding is None:
        return {"success": False, "error": f"No finding found with id {finding_id}"}

    github_thread_ids = _thread_ids_for_finding(finding)
    for comment_id in _comment_ids_for_finding(finding):
        thread_node_id = await fetch_review_thread_id_for_comment(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_comment_id=comment_id,
            token=token,
        )
        if thread_node_id and thread_node_id not in github_thread_ids:
            github_thread_ids.append(thread_node_id)
    if not github_thread_ids:
        return {"success": False, "error": "Could not resolve GitHub review thread id"}

    resolved_thread_ids = _str_list(finding.get("github_resolved_thread_ids"))
    posted_resolution_comment_ids = _int_list(finding.get("github_posted_resolution_comment_ids"))
    comment_ids = _comment_ids_for_finding(finding)
    primary_comment_id = comment_ids[0] if comment_ids else None
    unresolved_threads = [t for t in github_thread_ids if t not in resolved_thread_ids]
    if (
        unresolved_threads
        and primary_comment_id
        and primary_comment_id not in posted_resolution_comment_ids
    ):
        reply = await reply_to_review_comment(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_comment_id=primary_comment_id,
            body=render_resolution_comment(finding, status, note=note),
            token=token,
        )
        if reply and isinstance(reply.get("id"), int):
            posted_resolution_comment_ids.append(primary_comment_id)

    resolved_count = 0
    for github_thread_id in github_thread_ids:
        if github_thread_id in resolved_thread_ids:
            continue
        ok = await resolve_review_thread(thread_node_id=github_thread_id, token=token)
        if ok:
            resolved_thread_ids.append(github_thread_id)
            resolved_count += 1
    if resolved_count == 0 and not all(
        github_thread_id in resolved_thread_ids for github_thread_id in github_thread_ids
    ):
        return {"success": False, "error": "GitHub did not resolve the review thread"}

    updates: dict[str, Any] = {
        "status": status,
        "github_review_thread_id": github_thread_ids[0],
        "github_review_thread_ids": github_thread_ids,
        "github_resolved_thread_ids": resolved_thread_ids,
        "github_thread_resolved": all(
            github_thread_id in resolved_thread_ids for github_thread_id in github_thread_ids
        ),
    }
    if note:
        updates["last_reconciliation_note"] = note
    if posted_resolution_comment_ids:
        updates["github_posted_resolution_comment_ids"] = posted_resolution_comment_ids
    updated = await update_finding_fields(thread_id, finding_id, updates)
    surface_updates: dict[str, Any] = {
        "state": "resolved" if updates["github_thread_resolved"] else "resolve_pending",
        "github_review_thread_id": github_thread_ids[0],
        "last_error": None
        if updates["github_thread_resolved"]
        else "Not all GitHub threads resolved",
    }
    await update_finding_surface(thread_id, finding_id, surface_updates)
    updated = await get_finding(thread_id, finding_id)
    return {"success": True, "finding": updated, "resolved_thread_count": resolved_count}


async def _get_finding_with_pr_backfill(
    *,
    thread_id: str,
    finding_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> Finding | None:
    finding = await get_finding(thread_id, finding_id)
    if finding is None:
        return None
    if _thread_ids_for_finding(finding) or _comment_ids_for_finding(finding):
        return finding

    review_threads = await fetch_pr_review_threads(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
    )
    if review_threads:
        await reconcile_findings_with_review_threads(thread_id, review_threads)
        finding = await get_finding(thread_id, finding_id)
    return finding


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int)]


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _comment_ids_for_finding(finding: dict[str, Any]) -> list[int]:
    comment_ids = _int_list(finding.get("github_review_comment_ids"))
    comment_id = finding.get("github_review_comment_id")
    if isinstance(comment_id, int) and comment_id not in comment_ids:
        comment_ids.insert(0, comment_id)
    return comment_ids


def _thread_ids_for_finding(finding: dict[str, Any]) -> list[str]:
    thread_ids = _str_list(finding.get("github_review_thread_ids"))
    thread_id = finding.get("github_review_thread_id")
    if isinstance(thread_id, str) and thread_id and thread_id not in thread_ids:
        thread_ids.insert(0, thread_id)
    return thread_ids
