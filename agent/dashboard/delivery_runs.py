"""Delivery run rollup helpers for dashboard thread summaries."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_PR_STATES = frozenset({"draft", "open", "merged", "closed"})
_GITHUB_PR_URL_RE = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)")
_DELIVERY_DIRECT_KEYS = frozenset(
    {
        "delivery_queue_status",
        "queue_status",
        "delivery_worker_thread_id",
        "worker_thread_id",
        "reviewer_thread_id",
        "qa_thread_id",
        "merge_worker_thread_id",
        "merge_status",
        "gate_rollup",
        "blocker_reason",
        "preview_count",
        "artifact_count",
    }
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _store_value(record: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not record:
        return {}
    value = record.get("value")
    if isinstance(value, Mapping):
        return value
    return record


def _has_delivery_signal(*sources: Mapping[str, Any]) -> bool:
    return any(any(key in source for key in _DELIVERY_DIRECT_KEYS) for source in sources)


def _first_string(*sources_and_keys: tuple[Mapping[str, Any], tuple[str, ...]]) -> str | None:
    for source, keys in sources_and_keys:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return max(int(value), 0)
    return 0


def _first_count(*sources_and_keys: tuple[Mapping[str, Any], tuple[str, ...]]) -> int:
    for source, keys in sources_and_keys:
        for key in keys:
            value = source.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                return _count(value)
    return 0


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _gate_rollup(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str) and raw.strip():
        return {"status": raw.strip(), "passed": 0, "failed": 0, "pending": 0, "total": 0}
    gate = _mapping(raw)
    if not gate:
        return None
    passed = _count(gate.get("passed"))
    failed = _count(gate.get("failed"))
    pending = _count(gate.get("pending"))
    total = _count(gate.get("total")) or passed + failed + pending
    status = gate.get("status")
    return {
        "status": status.strip() if isinstance(status, str) and status.strip() else "unknown",
        "passed": passed,
        "failed": failed,
        "pending": pending,
        "total": total,
    }


def _first_gate_rollup(*sources: Mapping[str, Any]) -> dict[str, Any] | None:
    for source in sources:
        gate = _gate_rollup(source.get("gate_rollup") or source.get("gateRollup"))
        if gate:
            return gate
    return None


def _artifact_rollup(store_record: Mapping[str, Any], delivery: Mapping[str, Any]) -> list[Any]:
    worker_result = _mapping(store_record.get("worker_result"))
    qa_evidence = _mapping(store_record.get("qa_evidence")) or _mapping(
        worker_result.get("qa_evidence")
    )
    artifacts = [
        *_list(store_record.get("artifacts")),
        *_list(delivery.get("artifacts")),
        *_list(qa_evidence.get("screenshots")),
        *_list(qa_evidence.get("videos")),
        *_list(qa_evidence.get("traces")),
    ]
    return artifacts


def _gate_detail(store_record: Mapping[str, Any], delivery: Mapping[str, Any]) -> list[Any]:
    qa_evidence = _mapping(store_record.get("qa_evidence"))
    gates = _list(store_record.get("gates")) or _list(qa_evidence.get("gates"))
    return gates or _list(delivery.get("gates"))


def _blockers(store_record: Mapping[str, Any], delivery: Mapping[str, Any]) -> list[Any]:
    qa_evidence = _mapping(store_record.get("qa_evidence"))
    review_result = _mapping(store_record.get("review_result"))
    blockers = (
        _list(store_record.get("blockers"))
        or _list(qa_evidence.get("blockers"))
        or _list(review_result.get("blockers"))
    )
    return blockers or _list(delivery.get("blockers"))


def _merge_result(
    store_record: Mapping[str, Any], delivery: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    merge_audit = _mapping(store_record.get("merge_audit")) or _mapping(delivery.get("merge_audit"))
    if merge_audit:
        return merge_audit
    provider = _mapping(store_record.get("merge_result")) or _mapping(delivery.get("merge_result"))
    return provider or None


def _smoke_proof(
    store_record: Mapping[str, Any], delivery: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    proof = _mapping(store_record.get("smoke_proof")) or _mapping(delivery.get("smoke_proof"))
    if not proof:
        return None
    acceptance = _mapping(proof.get("acceptance"))
    result = {
        "status": proof.get("status"),
        "acceptance": dict(acceptance),
    }
    reason = proof.get("reason")
    if reason:
        result["reason"] = reason
    return result


def _pr_number_from_url(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = _GITHUB_PR_URL_RE.search(value)
    if not match:
        return None
    return int(match.group(1))


def _pr_rollup(
    metadata: Mapping[str, Any],
    delivery: Mapping[str, Any],
    store_record: Mapping[str, Any],
) -> dict[str, Any] | None:
    nested_pr = _mapping(store_record.get("pr")) or _mapping(delivery.get("pr"))
    pr_number = (
        nested_pr.get("number")
        or store_record.get("pr_number")
        or delivery.get("pr_number")
        or metadata.get("pr_number")
    )
    pr_url = (
        nested_pr.get("url")
        or store_record.get("pr_url")
        or store_record.get("pull_request_url")
        or store_record.get("pullRequestUrl")
        or delivery.get("pr_url")
        or delivery.get("pull_request_url")
        or delivery.get("pullRequestUrl")
        or metadata.get("pr_url")
        or metadata.get("pull_request_url")
        or metadata.get("pullRequestUrl")
    )
    if isinstance(pr_number, bool):
        return None
    if not isinstance(pr_number, int):
        pr_number = _pr_number_from_url(pr_url)
    if not isinstance(pr_number, int) or not isinstance(pr_url, str):
        return None

    title = _first_string(
        (nested_pr, ("title",)),
        (store_record, ("pr_title", "title")),
        (delivery, ("pr_title", "title")),
        (metadata, ("pr_title", "title")),
    )
    state = _first_string(
        (nested_pr, ("state",)),
        (store_record, ("pr_state",)),
        (delivery, ("pr_state",)),
        (metadata, ("pr_state",)),
    )
    head_ref = _first_string(
        (nested_pr, ("headRef", "head_ref")),
        (store_record, ("head_ref", "branch_name", "branch")),
        (delivery, ("head_ref", "branch_name", "branch")),
        (metadata, ("branch_name",)),
    )
    base_ref = _first_string(
        (nested_pr, ("baseRef", "base_ref")),
        (store_record, ("base_ref",)),
        (delivery, ("base_ref",)),
        (metadata, ("base_branch",)),
    )
    return {
        "number": pr_number,
        "title": title or "Untitled agent",
        "state": state if state in _PR_STATES else "open",
        "headRef": head_ref or "",
        "baseRef": base_ref or "main",
        "url": pr_url,
    }


def build_delivery_run_rollup(
    metadata: Mapping[str, Any],
    *,
    store_record: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the dashboard delivery rollup, if this thread carries delivery data."""

    delivery = _mapping(metadata.get("delivery")) or _mapping(metadata.get("delivery_run"))
    store = _store_value(store_record)
    if not delivery and not store and not _has_delivery_signal(metadata):
        return None

    return {
        "queueStatus": _first_string(
            (store, ("queue_status", "queueStatus", "status")),
            (delivery, ("queue_status", "queueStatus")),
            (metadata, ("delivery_queue_status", "queue_status")),
        ),
        "workerThreadId": _first_string(
            (store, ("worker_thread_id", "workerThreadId")),
            (delivery, ("worker_thread_id", "workerThreadId")),
            (metadata, ("delivery_worker_thread_id", "worker_thread_id")),
        ),
        "reviewerThreadId": _first_string(
            (store, ("reviewer_thread_id", "reviewerThreadId")),
            (delivery, ("reviewer_thread_id", "reviewerThreadId")),
            (metadata, ("reviewer_thread_id",)),
        ),
        "qaThreadId": _first_string(
            (store, ("qa_thread_id", "qaThreadId")),
            (delivery, ("qa_thread_id", "qaThreadId")),
            (metadata, ("qa_thread_id",)),
        ),
        "mergeWorkerThreadId": _first_string(
            (store, ("merge_worker_thread_id", "mergeWorkerThreadId", "merge_worker")),
            (delivery, ("merge_worker_thread_id", "mergeWorkerThreadId", "merge_worker")),
            (metadata, ("merge_worker_thread_id", "merge_worker")),
        ),
        "pr": _pr_rollup(metadata, delivery, store),
        "branch": _first_string(
            (store, ("branch", "branch_name", "head_ref")),
            (delivery, ("branch", "branch_name", "head_ref")),
            (metadata, ("branch_name",)),
        ),
        "previewUrl": _first_string(
            (_mapping(store.get("qa_evidence")), ("preview_url", "previewUrl")),
            (store, ("preview_url", "previewUrl")),
            (delivery, ("preview_url", "previewUrl")),
            (metadata, ("preview_url",)),
        ),
        "previewCount": _first_count(
            (store, ("preview_count", "previewCount")),
            (delivery, ("preview_count", "previewCount")),
            (metadata, ("preview_count",)),
        ),
        "artifactCount": _first_count(
            (store, ("artifact_count", "artifactCount")),
            (delivery, ("artifact_count", "artifactCount")),
            (metadata, ("artifact_count",)),
        ),
        "gateRollup": _first_gate_rollup(store, delivery, metadata),
        "gates": _gate_detail(store, delivery),
        "artifacts": _artifact_rollup(store, delivery),
        "blockers": _blockers(store, delivery),
        "blockerReason": _first_string(
            (store, ("blocker_reason", "blockerReason")),
            (delivery, ("blocker_reason", "blockerReason")),
            (metadata, ("blocker_reason",)),
        ),
        "reviewedSha": _first_string(
            (store, ("reviewed_sha", "reviewedSha", "last_reviewed_sha")),
            (delivery, ("reviewed_sha", "reviewedSha", "last_reviewed_sha")),
            (metadata, ("reviewed_sha", "last_reviewed_sha")),
        ),
        "mergeStatus": _first_string(
            (store, ("merge_status", "mergeStatus")),
            (delivery, ("merge_status", "mergeStatus")),
            (metadata, ("merge_status",)),
        ),
        "mergeResult": _merge_result(store, delivery),
        "smokeProof": _smoke_proof(store, delivery),
    }
