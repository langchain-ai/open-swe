"""Driving one finding's GitHub review threads to resolved.

Two callers need the same state machine — the publish flow sweeps every finding
a run marked resolved, and ``update_finding`` / ``resolve_finding_thread``
resolve a single finding on demand — so it lives here once: discover any thread
ids the finding is missing, post the resolution reply on each thread's primary
comment exactly once, resolve the thread, and report the fields the caller must
persist.
"""

from dataclasses import dataclass
from typing import Any

from .findings import (
    SURFACE_STATE_ORDER,
    Finding,
    SurfaceState,
    comment_ids_for_finding,
    get_finding,
    posted_resolution_comment_ids_for_finding,
    resolved_thread_ids_for_finding,
    surface_state_of,
    thread_ids_for_finding,
    update_finding_fields,
)
from .publish import (
    fetch_review_thread_id_for_comment,
    render_resolution_comment,
    reply_to_review_comment,
    resolve_review_thread,
)
from .reconcile import sync_findings_with_github


@dataclass(frozen=True)
class ThreadResolution:
    """What resolving one finding's GitHub threads did.

    ``fields`` is the canonical publication identity to write back; ``changed``
    says whether anything actually moved on GitHub, so a caller that owns the
    whole findings list can skip a pointless write.
    """

    resolved_count: int
    fields: dict[str, Any]
    changed: bool

    @property
    def fully_resolved(self) -> bool:
        return self.fields.get("surface_state") == "resolved"


async def resolve_github_threads_for_finding(
    finding: Finding,
    *,
    status: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    note: str | None = None,
) -> ThreadResolution:
    """Post the resolution reply and resolve every unresolved thread of ``finding``.

    A finding can own several threads when an earlier run duplicated its comment
    before publication identity was backfilled, so every one of them is walked.
    """
    thread_node_ids = thread_ids_for_finding(finding)
    comment_ids = comment_ids_for_finding(finding)
    discovered = False
    for comment_id in comment_ids:
        thread_node_id = await fetch_review_thread_id_for_comment(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            review_comment_id=comment_id,
            token=token,
        )
        if thread_node_id and thread_node_id not in thread_node_ids:
            thread_node_ids.append(thread_node_id)
            discovered = True

    if not thread_node_ids:
        return ThreadResolution(resolved_count=0, fields={}, changed=False)

    resolved_thread_ids = resolved_thread_ids_for_finding(finding)
    posted_comment_ids = posted_resolution_comment_ids_for_finding(finding)
    resolution_body = render_resolution_comment(finding, status, note=note)

    resolved_count = 0
    changed = discovered
    for index, thread_node_id in enumerate(thread_node_ids):
        if thread_node_id in resolved_thread_ids:
            continue
        primary_comment_id = comment_ids[index] if index < len(comment_ids) else None
        if (
            primary_comment_id is not None
            and primary_comment_id not in posted_comment_ids
            and resolution_body is not None
        ):
            reply = await reply_to_review_comment(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                review_comment_id=primary_comment_id,
                body=resolution_body,
                token=token,
            )
            if reply and isinstance(reply.get("id"), int):
                posted_comment_ids.append(primary_comment_id)
                changed = True
        if await resolve_review_thread(thread_node_id=thread_node_id, token=token):
            resolved_thread_ids.append(thread_node_id)
            resolved_count += 1
            changed = True

    fields: dict[str, Any] = {"github_review_thread_ids": thread_node_ids}
    if resolved_thread_ids:
        fields["github_resolved_thread_ids"] = resolved_thread_ids
    if posted_comment_ids:
        fields["github_posted_resolution_comment_ids"] = posted_comment_ids
    reached: SurfaceState = (
        "resolved"
        if all(node_id in resolved_thread_ids for node_id in thread_node_ids)
        else "resolve_pending"
    )
    # Surface states only move forward, so never regress a finding that a
    # previous run already carried further along.
    states: list[SurfaceState] = [reached, surface_state_of(finding)]
    fields["surface_state"] = max(states, key=lambda state: SURFACE_STATE_ORDER[state])
    return ThreadResolution(resolved_count=resolved_count, fields=fields, changed=changed)


async def resolve_finding_on_github(
    *,
    thread_id: str,
    finding_id: str,
    status: str,
    note: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> dict[str, Any]:
    """Resolve one tracked finding's GitHub threads and persist the transition."""
    finding = await _finding_with_pr_backfill(
        thread_id=thread_id,
        finding_id=finding_id,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
    )
    if finding is None:
        return {"success": False, "error": f"No finding found with id {finding_id}"}

    resolution = await resolve_github_threads_for_finding(
        finding,
        status=status,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        token=token,
        note=note,
    )
    if not resolution.fields:
        return {"success": False, "error": "Could not resolve GitHub review thread id"}
    if resolution.resolved_count == 0 and not resolution.fully_resolved:
        return {"success": False, "error": "GitHub did not resolve the review thread"}

    updates: dict[str, Any] = {
        **resolution.fields,
        "status": status,
        "last_reconciliation_note": note,
        "resolution_note": note,
    }
    updated = await update_finding_fields(thread_id, finding_id, updates)
    return {
        "success": True,
        "finding": updated,
        "resolved_thread_count": resolution.resolved_count,
    }


async def _finding_with_pr_backfill(
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
    await sync_findings_with_github(
        thread_id, owner=owner, repo=repo, pr_number=pr_number, token=token
    )
    return await get_finding(thread_id, finding_id)
