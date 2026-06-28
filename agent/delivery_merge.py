"""Execute policy-approved delivery merges through a dedicated merge worker."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .delivery_queue import read_delivery_queue_item, transition_delivery_queue_status
from .merge_controller import MergeResult, evaluate_auto_merge, merge_pr

MergeExecutor = Callable[..., Awaitable[MergeResult]]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _merge_worker_thread_id_for(item: Mapping[str, Any]) -> str:
    existing = _string(item.get("merge_worker_thread_id"))
    if existing:
        return existing
    item_id = _string(item.get("id"))
    digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16]
    return f"delivery-merge-{digest}"


def _reviewed_sha(item: Mapping[str, Any]) -> str:
    review_result = _mapping(item.get("review_result"))
    return _string(item.get("reviewed_sha") or review_result.get("reviewed_sha"))


def _repo(item: Mapping[str, Any], pr: Mapping[str, Any]) -> dict[str, str]:
    repo = _mapping(item.get("repo"))
    owner = _string(repo.get("owner"))
    name = _string(repo.get("name"))
    if owner and name:
        return {"owner": owner, "name": name}
    base = _mapping(pr.get("base"))
    base_repo = _mapping(base.get("repo"))
    full_name = _string(base_repo.get("full_name"))
    if "/" in full_name:
        owner, name = full_name.split("/", 1)
        return {"owner": owner, "name": name}
    owner_mapping = _mapping(base_repo.get("owner"))
    owner = _string(owner_mapping.get("login") or base_repo.get("owner"))
    name = _string(base_repo.get("name"))
    return {"owner": owner, "name": name}


def _pr_number(pr: Mapping[str, Any]) -> int | None:
    number = pr.get("number")
    return number if isinstance(number, int) else None


def _blocking_gates(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    qa_evidence = _mapping(item.get("qa_evidence"))
    gate_policy = _mapping(item.get("gate_policy"))
    names = {
        gate.strip()
        for gate in _list(gate_policy.get("blocking_gates"))
        if isinstance(gate, str) and gate.strip()
    }
    gates = [gate for gate in _list(qa_evidence.get("gates")) if isinstance(gate, Mapping)]
    if not names:
        return gates
    return [gate for gate in gates if _string(gate.get("name") or gate.get("id")) in names]


def _qa_checks(item: Mapping[str, Any]) -> list[Mapping[str, Any] | bool]:
    review_result = _mapping(item.get("review_result"))
    qa_result = _mapping(review_result.get("qa_result"))
    return [qa_result] if qa_result else []


def _required_checks(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [check for check in _list(item.get("required_checks")) if isinstance(check, Mapping)]


def _merge_policy(item: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(item.get("merge_policy"))


def _merge_audit(
    *,
    merge_worker_thread_id: str,
    status: str,
    reason: str,
    merge_strategy: str,
    target_branch: str,
    credential_identity: str,
    decision: Mapping[str, Any],
    result: MergeResult | None = None,
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "merge_worker_thread_id": merge_worker_thread_id,
        "strategy": merge_strategy,
        "target_branch": target_branch,
        "credential_identity": credential_identity,
        "decision": dict(decision),
    }
    if result is not None:
        audit["provider"] = {
            "status": result.status,
            "reason": result.reason,
            "sha": result.sha,
            "http_status": result.http_status,
            "details": result.details,
        }
    return audit


async def execute_delivery_merge(
    item_id: str,
    *,
    token: str | None,
    pr: Mapping[str, Any] | None = None,
    merge_func: MergeExecutor | None = None,
) -> dict[str, Any]:
    item = await read_delivery_queue_item(item_id)
    if item is None:
        raise KeyError(f"delivery queue item not found: {item_id}")

    merge_policy = _merge_policy(item)
    pr_snapshot = _mapping(pr) or _mapping(item.get("pr"))
    strategy = _string(merge_policy.get("strategy")) or "squash"
    target_branch = (
        _string(merge_policy.get("target_branch") or merge_policy.get("base_branch")) or "main"
    )
    merge_worker_thread_id = _merge_worker_thread_id_for(item)
    credential_identity = _string(
        item.get("merge_credential_identity") or item.get("credential_identity")
    )
    reviewed_sha = _reviewed_sha(item)
    decision = evaluate_auto_merge(
        pr=pr_snapshot,
        expected_head_sha=reviewed_sha,
        implementation_thread_id=_string(item.get("worker_thread_id")),
        reviewer_thread_id=_string(item.get("reviewer_thread_id")),
        reviewed_sha=reviewed_sha,
        qa_evidence=_mapping(item.get("qa_evidence")),
        findings=[
            finding for finding in _list(item.get("findings")) if isinstance(finding, Mapping)
        ],
        required_checks=_required_checks(item),
        required_check_names=merge_policy.get("required_checks")
        if isinstance(merge_policy.get("required_checks"), Sequence)
        else None,
        qa_checks=_qa_checks(item),
        blocking_gates=_blocking_gates(item),
        merge_policy_enabled=merge_policy.get("enabled") is True,
        credential_available=bool(token),
        kill_switch=bool(merge_policy.get("kill_switch") or item.get("kill_switch")),
        merge_method=strategy,
        target_branch=target_branch,
    )
    decision_audit = {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "blockers": list(decision.blockers),
        "head_sha": decision.head_sha,
        "merge_method": decision.merge_method,
        "pr_number": decision.pr_number,
    }
    if not decision.allowed:
        audit = _merge_audit(
            merge_worker_thread_id=merge_worker_thread_id,
            status="blocked",
            reason=decision.reason,
            merge_strategy=strategy,
            target_branch=target_branch,
            credential_identity=credential_identity,
            decision=decision_audit,
        )
        return await transition_delivery_queue_status(
            item_id,
            "blocked",
            reason=decision.reason,
            extra={
                "merge_worker_thread_id": merge_worker_thread_id,
                "merge_status": "blocked",
                "merge_audit": audit,
                "blockers": [
                    {"code": blocker, "message": blocker} for blocker in decision.blockers
                ],
                "blocker_reason": decision.reason,
                "delivery": {
                    **_mapping(item.get("delivery")),
                    "queue_status": "blocked",
                    "merge_worker_thread_id": merge_worker_thread_id,
                    "merge_status": "blocked",
                    "blocker_reason": decision.reason,
                },
            },
        )

    repo = _repo(item, pr_snapshot)
    pr_number = _pr_number(pr_snapshot)
    if not repo["owner"] or not repo["name"] or pr_number is None:
        raise ValueError("merge requires repository owner/name and PR number")

    result = await merge_pr(
        owner=repo["owner"],
        repo=repo["name"],
        pr_number=pr_number,
        token=token,
        decision=decision,
        merge_func=merge_func,
    )
    if result.success:
        merged_at = _now_iso()
        audit = _merge_audit(
            merge_worker_thread_id=merge_worker_thread_id,
            status="merged",
            reason=result.reason,
            merge_strategy=strategy,
            target_branch=target_branch,
            credential_identity=credential_identity,
            decision=decision_audit,
            result=result,
        )
        return await transition_delivery_queue_status(
            item_id,
            "done",
            reason="merge completed",
            extra={
                "merge_worker_thread_id": merge_worker_thread_id,
                "merge_status": "merged",
                "merge_commit_sha": result.sha,
                "merge_strategy": strategy,
                "target_branch": target_branch,
                "merged_at": merged_at,
                "merge_credential_identity": credential_identity,
                "merge_audit": audit,
                "delivery": {
                    **_mapping(item.get("delivery")),
                    "queue_status": "done",
                    "merge_worker_thread_id": merge_worker_thread_id,
                    "merge_status": "merged",
                },
            },
        )

    queue_status = "blocked" if result.status == "blocked" else "failed"
    audit = _merge_audit(
        merge_worker_thread_id=merge_worker_thread_id,
        status=result.status,
        reason=result.reason,
        merge_strategy=strategy,
        target_branch=target_branch,
        credential_identity=credential_identity,
        decision=decision_audit,
        result=result,
    )
    return await transition_delivery_queue_status(
        item_id,
        queue_status,
        reason=result.reason,
        extra={
            "merge_worker_thread_id": merge_worker_thread_id,
            "merge_status": result.status,
            "merge_audit": audit,
            "blocker_reason": result.reason,
            "delivery": {
                **_mapping(item.get("delivery")),
                "queue_status": queue_status,
                "merge_worker_thread_id": merge_worker_thread_id,
                "merge_status": result.status,
                "blocker_reason": result.reason,
            },
        },
    )
