"""Launch delivery reviewer and QA checks, then persist review outcomes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .delivery_queue import read_delivery_queue_item, transition_delivery_queue_status
from .dispatch import dispatch_agent_run
from .utils.thread_ops import langgraph_client

DELIVERY_REVIEW_SOURCE = "delivery_review"
_TERMINAL_FINDING_STATUSES = frozenset({"resolved", "dismissed"})
_BLOCKING_FINDING_SEVERITIES = frozenset({"high", "critical"})


def _client() -> Any:
    return langgraph_client()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _model_selection(item: Mapping[str, Any], role: str) -> dict[str, Any]:
    snapshot = _mapping(item.get("model_routing_snapshot"))
    roles = _mapping(snapshot.get("roles"))
    return _mapping(roles.get(role))


def _lower(value: Any) -> str:
    return value.lower() if isinstance(value, str) else ""


def _thread_digest(item_id: str) -> str:
    return hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16]


def _reviewer_thread_id_for(item: Mapping[str, Any]) -> str:
    existing = _string(item.get("reviewer_thread_id"))
    if existing:
        return existing
    return f"delivery-reviewer-{_thread_digest(_string(item.get('id')))}"


def _qa_thread_id_for(item: Mapping[str, Any]) -> str:
    existing = _string(item.get("qa_thread_id"))
    if existing:
        return existing
    return f"delivery-qa-{_thread_digest(_string(item.get('id')))}"


def _qa_required(item: Mapping[str, Any]) -> bool:
    gate_policy = _mapping(item.get("gate_policy"))
    qa_evidence = _mapping(item.get("qa_evidence"))
    return (
        gate_policy.get("qa_evidence") is True
        or qa_evidence.get("browser_relevant") is True
        or bool(gate_policy.get("qa_agent"))
    )


def _review_context(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "queue_item_id": _string(item.get("id")),
        "worker_thread_id": _string(item.get("worker_thread_id")),
        "pr": _mapping(item.get("pr")),
        "worker_result": _mapping(item.get("worker_result")),
        "qa_evidence": _mapping(item.get("qa_evidence")),
        "gate_rollup": _mapping(item.get("gate_rollup")),
    }


def _prompt(title: str, context: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            title,
            "",
            "Review context:",
            json.dumps(context, indent=2, sort_keys=True),
        ]
    )


def _reviewer_configurable(
    item: Mapping[str, Any],
    *,
    reviewer_thread_id: str,
    qa_required: bool,
) -> dict[str, Any]:
    configurable = {
        "thread_id": reviewer_thread_id,
        "source": DELIVERY_REVIEW_SOURCE,
        "delivery_queue_item_id": _string(item.get("id")),
        "worker_thread_id": _string(item.get("worker_thread_id")),
        "qa_required": qa_required,
        "review_context": _review_context(item),
    }
    selection = _model_selection(item, "qa_reviewer")
    if model_id := _string(selection.get("model_id")):
        configurable["reviewer_model_id"] = model_id
    if effort := _string(selection.get("effort")):
        configurable["reviewer_effort"] = effort
    return configurable


def _qa_configurable(item: Mapping[str, Any], *, qa_thread_id: str) -> dict[str, Any]:
    configurable = {
        "thread_id": qa_thread_id,
        "source": DELIVERY_REVIEW_SOURCE,
        "delivery_queue_item_id": _string(item.get("id")),
        "worker_thread_id": _string(item.get("worker_thread_id")),
        "qa_context": _review_context(item),
    }
    selection = _model_selection(item, "vision")
    if model_id := _string(selection.get("model_id")):
        configurable["agent_model_id"] = model_id
    if effort := _string(selection.get("effort")):
        configurable["agent_effort"] = effort
    return configurable


def _unresolved_blocking_finding(finding: Mapping[str, Any]) -> bool:
    if _lower(finding.get("status") or "open") in _TERMINAL_FINDING_STATUSES:
        return False
    if finding.get("blocking") is True:
        return True
    return _lower(finding.get("severity")) in _BLOCKING_FINDING_SEVERITIES


def _finding_label(finding: Mapping[str, Any]) -> str:
    for key in ("id", "title", "file"):
        value = _string(finding.get(key))
        if value:
            return value
    return "unknown"


async def _upsert_thread_metadata(client: Any, thread_id: str, metadata: dict[str, Any]) -> None:
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="do_nothing")
    await client.threads.update(thread_id=thread_id, metadata=metadata)


async def launch_delivery_review_checks(
    item_id: str,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    item = await read_delivery_queue_item(item_id)
    if item is None:
        return {"status": "refused", "reason": "missing_queue_item", "item_id": item_id}
    if item.get("status") != "review":
        return {
            "status": "refused",
            "reason": "not_reviewable",
            "item_id": item_id,
            "current_status": item.get("status"),
        }

    reviewer_thread_id = _reviewer_thread_id_for(item)
    worker_thread_id = _string(item.get("worker_thread_id"))
    if not reviewer_thread_id or reviewer_thread_id == worker_thread_id:
        await transition_delivery_queue_status(
            item_id,
            "blocked",
            reason="self_review_refused",
            extra={"blocker_reason": "self_review_refused"},
        )
        return {
            "status": "refused",
            "reason": "self_review_refused",
            "item_id": item_id,
            "reviewer_thread_id": reviewer_thread_id,
            "worker_thread_id": worker_thread_id,
        }

    qa_required = _qa_required(item)
    qa_evidence = _mapping(item.get("qa_evidence"))
    if qa_required and qa_evidence.get("complete") is not True:
        await transition_delivery_queue_status(
            item_id,
            "blocked",
            reason="qa_evidence_missing",
            extra={"blocker_reason": "qa_evidence_missing"},
        )
        return {
            "status": "refused",
            "reason": "qa_evidence_missing",
            "item_id": item_id,
        }

    client = client or _client()
    qa_thread_id = _qa_thread_id_for(item) if qa_required else None
    context = _review_context(item)
    reviewer_metadata = {
        "source": DELIVERY_REVIEW_SOURCE,
        "delivery_queue_item_id": item_id,
        "worker_thread_id": worker_thread_id,
        "reviewer_thread_id": reviewer_thread_id,
        "qa_thread_id": qa_thread_id,
        "delivery": {
            **_mapping(item.get("delivery")),
            "queue_status": "review",
            "reviewer_thread_id": reviewer_thread_id,
            "qa_thread_id": qa_thread_id,
        },
    }
    if model_routing_snapshot := _mapping(item.get("model_routing_snapshot")):
        reviewer_metadata["model_routing_snapshot"] = model_routing_snapshot
    await _upsert_thread_metadata(client, reviewer_thread_id, reviewer_metadata)
    reviewer_run = await dispatch_agent_run(
        reviewer_thread_id,
        _prompt(
            "Run an independent delivery review, then call submit_delivery_review_result.",
            context,
        ),
        _reviewer_configurable(
            item, reviewer_thread_id=reviewer_thread_id, qa_required=qa_required
        ),
        source=DELIVERY_REVIEW_SOURCE,
        assistant_id="reviewer",
        metadata=reviewer_metadata,
        client=client,
    )

    qa_run_id = None
    if qa_thread_id:
        qa_metadata = {
            **reviewer_metadata,
            "qa_thread_id": qa_thread_id,
            "reviewer_thread_id": reviewer_thread_id,
        }
        await _upsert_thread_metadata(client, qa_thread_id, qa_metadata)
        qa_run = await dispatch_agent_run(
            qa_thread_id,
            _prompt(
                "Run configured delivery QA checks, then call submit_delivery_qa_result.", context
            ),
            _qa_configurable(item, qa_thread_id=qa_thread_id),
            source=DELIVERY_REVIEW_SOURCE,
            assistant_id="agent",
            metadata=qa_metadata,
            client=client,
        )
        qa_run_id = qa_run.get("run_id") if isinstance(qa_run, Mapping) else None

    reviewer_run_id = reviewer_run.get("run_id") if isinstance(reviewer_run, Mapping) else None
    review_result = {
        "status": "pending",
        "reviewer_thread_id": reviewer_thread_id,
        "reviewer_run_id": reviewer_run_id,
        "qa_required": qa_required,
        "qa_thread_id": qa_thread_id,
        "qa_run_id": qa_run_id,
    }
    updated = await transition_delivery_queue_status(
        item_id,
        "review",
        reason="review checks dispatched",
        extra={
            "reviewer_thread_id": reviewer_thread_id,
            "qa_thread_id": qa_thread_id,
            "review_result": review_result,
            "merge_eligible": False,
            "delivery": reviewer_metadata["delivery"],
        },
    )
    return {
        "status": "launched",
        "item_id": item_id,
        "reviewer_thread_id": reviewer_thread_id,
        "reviewer_run_id": reviewer_run_id,
        "qa_thread_id": qa_thread_id,
        "qa_run_id": qa_run_id,
        "queue_status": updated["status"],
    }


async def record_delivery_review_result(
    item_id: str,
    review: Mapping[str, Any],
) -> dict[str, Any]:
    item = await read_delivery_queue_item(item_id)
    if item is None:
        raise KeyError(f"delivery queue item not found: {item_id}")

    findings = [
        dict(finding) for finding in _list(review.get("findings")) if isinstance(finding, Mapping)
    ]
    blockers = [
        {
            "code": f"unresolved_blocking_finding:{_finding_label(finding)}",
            "message": "Review has an unresolved blocking finding.",
        }
        for finding in findings
        if _unresolved_blocking_finding(finding)
    ]
    qa_thread_id = _string(item.get("qa_thread_id"))
    qa_result = _mapping(review.get("qa_result"))
    if qa_thread_id and not qa_result:
        blockers.append({"code": "qa_check_missing", "message": "Required QA check is missing."})
    elif qa_result and qa_result.get("passed") is not True:
        blockers.append({"code": "qa_check_failed", "message": "Required QA check failed."})

    reviewed_sha = _string(review.get("reviewed_sha"))
    if not reviewed_sha:
        pr = _mapping(item.get("pr"))
        head = _mapping(pr.get("head"))
        reviewed_sha = _string(head.get("sha") or pr.get("head_sha"))

    review_result = {
        "status": "blocked" if blockers else "passed",
        "reviewed_sha": reviewed_sha,
        "findings": findings,
        "evidence_snapshot": _mapping(item.get("qa_evidence")),
        "blocking": bool(blockers),
        "blockers": blockers,
        "qa_result": qa_result,
        "reviewer_thread_id": _string(item.get("reviewer_thread_id")),
        "qa_thread_id": qa_thread_id,
    }
    queue_status = "blocked" if blockers else "review"
    reason = blockers[0]["code"] if blockers else "review passed"
    extra = {
        "review_result": review_result,
        "reviewed_sha": reviewed_sha,
        "findings": findings,
        "merge_eligible": not blockers,
        "blockers": blockers,
        "blocker_reason": reason if blockers else None,
        "delivery": {
            **_mapping(item.get("delivery")),
            "queue_status": queue_status,
            "reviewer_thread_id": _string(item.get("reviewer_thread_id")),
            "qa_thread_id": qa_thread_id,
            "reviewed_sha": reviewed_sha,
            "blocker_reason": reason if blockers else None,
        },
    }
    return await transition_delivery_queue_status(
        item_id,
        queue_status,
        reason=reason,
        extra=extra,
    )
