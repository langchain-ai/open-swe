from typing import Any

from langgraph.config import get_config

from agent.auth.thread_token import get_github_token
from agent.review.findings import (
    Finding,
    ReviewerThreadMissingError,
    comment_ids_for_finding,
    get_finding,
    get_thread_id_from_runtime,
    posted_resolution_comment_ids_for_finding,
    resolved_thread_ids_for_finding,
    thread_ids_for_finding,
    thread_missing_tool_result,
    update_finding_fields,
)
from agent.review.publish import (
    fetch_pr_review_threads,
    fetch_review_thread_id_for_comment,
    render_resolution_comment,
    reply_to_review_comment,
    resolve_review_thread,
)
from agent.review.reconcile import reconcile_findings_with_review_threads
from agent.utils.reviewer_outcomes import emit_finding_status_outcome


def _normalize_note(note: str | None) -> str | None:
    if note is None:
        return None
    normalized = note.strip()
    return normalized or None


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
    if status not in {"resolved", "dismissed"}:
        return {"success": False, "error": f"Invalid status: {status}"}
    normalized_note = _normalize_note(note)
    if normalized_note is None:
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
        result = await _resolve_finding_thread_async(
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


async def _resolve_finding_thread_async(
    *,
    finding_id: str,
    status: str,
    note: str,
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

    github_thread_ids = thread_ids_for_finding(finding)
    for comment_id in comment_ids_for_finding(finding):
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

    resolved_thread_ids = resolved_thread_ids_for_finding(finding)
    posted_resolution_comment_ids = posted_resolution_comment_ids_for_finding(finding)
    comment_ids = comment_ids_for_finding(finding)
    resolution_body = render_resolution_comment(finding, status, note=note)
    if resolution_body is None:
        return {"success": False, "error": "Missing resolution note"}

    resolved_count = 0
    for idx, github_thread_id in enumerate(github_thread_ids):
        if github_thread_id in resolved_thread_ids:
            continue
        primary_comment_id = comment_ids[idx] if idx < len(comment_ids) else None
        if primary_comment_id and primary_comment_id not in posted_resolution_comment_ids:
            reply = await reply_to_review_comment(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                review_comment_id=primary_comment_id,
                body=resolution_body,
                token=token,
            )
            if reply and isinstance(reply.get("id"), int):
                posted_resolution_comment_ids.append(primary_comment_id)
        ok = await resolve_review_thread(thread_node_id=github_thread_id, token=token)
        if ok:
            resolved_thread_ids.append(github_thread_id)
            resolved_count += 1
    if resolved_count == 0 and not all(
        github_thread_id in resolved_thread_ids for github_thread_id in github_thread_ids
    ):
        return {"success": False, "error": "GitHub did not resolve the review thread"}

    fully_resolved = all(
        github_thread_id in resolved_thread_ids for github_thread_id in github_thread_ids
    )
    updates: dict[str, Any] = {
        "status": status,
        "github_review_thread_ids": github_thread_ids,
        "github_resolved_thread_ids": resolved_thread_ids,
        "surface_state": "resolved" if fully_resolved else "resolve_pending",
        "last_reconciliation_note": note,
        "resolution_note": note,
    }
    if posted_resolution_comment_ids:
        updates["github_posted_resolution_comment_ids"] = posted_resolution_comment_ids
    updated = await update_finding_fields(thread_id, finding_id, updates)
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
    if thread_ids_for_finding(finding) or comment_ids_for_finding(finding):
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
