"""Workflow-file push approval state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client

WORKFLOW_PUSH_APPROVALS_KEY = "workflow_push_approvals"
WORKFLOW_APPROVAL_PENDING = "pending"
WORKFLOW_APPROVAL_APPROVED = "approved"
WORKFLOW_APPROVAL_REJECTED = "rejected"
_MAX_APPROVAL_RECORDS = 20


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _approvals_from_metadata(metadata: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = metadata.get(WORKFLOW_PUSH_APPROVALS_KEY) if metadata else None
    if not isinstance(raw, dict):
        return {}
    approvals: dict[str, dict[str, Any]] = {}
    for fingerprint, value in raw.items():
        if isinstance(fingerprint, str) and fingerprint and isinstance(value, dict):
            record = dict(value)
            record.setdefault("fingerprint", fingerprint)
            approvals[fingerprint] = record
    return approvals


async def get_workflow_push_approvals(thread_id: str) -> dict[str, dict[str, Any]]:
    client = get_client()
    thread = await client.threads.get(thread_id)
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    return _approvals_from_metadata(metadata if isinstance(metadata, dict) else None)


async def workflow_push_approved(thread_id: str, fingerprint: str) -> bool:
    approvals = await get_workflow_push_approvals(thread_id)
    return approvals.get(fingerprint, {}).get("status") == WORKFLOW_APPROVAL_APPROVED


async def ensure_workflow_push_pending(
    thread_id: str,
    *,
    fingerprint: str,
    repo: str,
    branch: str,
    base_sha: str,
    head_sha: str,
    files: list[str],
) -> tuple[dict[str, Any], bool]:
    """Store a pending approval unless a terminal record already exists."""
    approvals = await get_workflow_push_approvals(thread_id)
    existing = approvals.get(fingerprint)
    if existing and existing.get("status") in {
        WORKFLOW_APPROVAL_PENDING,
        WORKFLOW_APPROVAL_APPROVED,
        WORKFLOW_APPROVAL_REJECTED,
    }:
        return existing, False

    record = {
        "fingerprint": fingerprint,
        "status": WORKFLOW_APPROVAL_PENDING,
        "repo": repo,
        "branch": branch,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "files": files,
        "requested_at": _now(),
        "notified": False,
    }
    approvals[fingerprint] = record
    await _save_approvals(thread_id, approvals)
    return record, True


async def mark_workflow_push_notified(thread_id: str, fingerprint: str) -> None:
    approvals = await get_workflow_push_approvals(thread_id)
    record = approvals.get(fingerprint)
    if not record:
        return
    record["notified"] = True
    record["notified_at"] = _now()
    approvals[fingerprint] = record
    await _save_approvals(thread_id, approvals)


async def decide_workflow_push_approval(
    thread_id: str,
    fingerprint: str,
    *,
    approved: bool,
    actor: str,
) -> dict[str, Any] | None:
    approvals = await get_workflow_push_approvals(thread_id)
    record = approvals.get(fingerprint)
    if not record:
        return None
    record["status"] = WORKFLOW_APPROVAL_APPROVED if approved else WORKFLOW_APPROVAL_REJECTED
    record["decided_at"] = _now()
    record["decided_by"] = actor
    approvals[fingerprint] = record
    await _save_approvals(thread_id, approvals)
    return record


async def _save_approvals(thread_id: str, approvals: dict[str, dict[str, Any]]) -> None:
    ordered = sorted(approvals.values(), key=lambda r: str(r.get("requested_at", "")))
    trimmed = ordered[-_MAX_APPROVAL_RECORDS:]
    await get_client().threads.update(
        thread_id=thread_id,
        metadata={WORKFLOW_PUSH_APPROVALS_KEY: {str(r["fingerprint"]): r for r in trimmed}},
    )
