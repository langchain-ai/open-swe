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
_TERMINAL_STATUSES = {WORKFLOW_APPROVAL_APPROVED, WORKFLOW_APPROVAL_REJECTED}


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
    diff_stats: Mapping[str, Any] | None = None,
    diff_preview: str | None = None,
    diff_preview_truncated: bool = False,
    approval_url: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Store a pending approval unless a terminal record already exists."""
    approvals = await get_workflow_push_approvals(thread_id)
    existing = approvals.get(fingerprint)
    if existing and existing.get("status") in _TERMINAL_STATUSES:
        return existing, False

    review_fields = {
        "repo": repo,
        "branch": branch,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "files": list(files),
        "diff_stats": _normalize_diff_stats(diff_stats, len(files)),
        "diff_preview": diff_preview or "",
        "diff_preview_truncated": diff_preview_truncated,
        "approval_url": approval_url,
    }
    if existing and existing.get("status") == WORKFLOW_APPROVAL_PENDING:
        record = {**existing, **review_fields}
        approvals[fingerprint] = record
        await _save_approvals(thread_id, approvals)
        return record, False

    record = {
        "fingerprint": fingerprint,
        "status": WORKFLOW_APPROVAL_PENDING,
        **review_fields,
        "requested_at": _now(),
        "notified": False,
    }
    approvals[fingerprint] = record
    await _save_approvals(thread_id, approvals)
    return record, True


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _normalize_diff_stats(value: Mapping[str, Any] | None, file_count: int) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {"files": file_count, "additions": 0, "deletions": 0}
    return {
        "files": _safe_int(value.get("files"), file_count),
        "additions": _safe_int(value.get("additions")),
        "deletions": _safe_int(value.get("deletions")),
    }


def workflow_push_approval_response(record: Mapping[str, Any]) -> dict[str, Any]:
    files = record.get("files")
    diff_stats = record.get("diff_stats")
    requested_at = record.get("requested_at")
    decided_at = record.get("decided_at")
    decided_by = record.get("decided_by")
    approval_url = record.get("approval_url")
    return {
        "fingerprint": str(record.get("fingerprint") or ""),
        "status": str(record.get("status") or WORKFLOW_APPROVAL_PENDING),
        "repo": str(record.get("repo") or ""),
        "branch": str(record.get("branch") or ""),
        "baseSha": str(record.get("base_sha") or ""),
        "headSha": str(record.get("head_sha") or ""),
        "files": [str(path) for path in files] if isinstance(files, list) else [],
        "diffStats": _normalize_diff_stats(
            diff_stats if isinstance(diff_stats, Mapping) else None,
            len(files) if isinstance(files, list) else 0,
        ),
        "diffPreview": str(record.get("diff_preview") or ""),
        "diffPreviewTruncated": record.get("diff_preview_truncated") is True,
        "approvalUrl": approval_url if isinstance(approval_url, str) and approval_url else None,
        "requestedAt": requested_at if isinstance(requested_at, str) else None,
        "decidedAt": decided_at if isinstance(decided_at, str) else None,
        "decidedBy": decided_by if isinstance(decided_by, str) else None,
    }


def workflow_push_approval_responses(
    approvals: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(approvals.values(), key=lambda r: str(r.get("requested_at", "")), reverse=True)
    return [workflow_push_approval_response(record) for record in ordered]


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
