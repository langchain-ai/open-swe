"""Route system-thread followups through incident admission and access checks."""

import time
from datetime import UTC, datetime
from typing import Any

from agent.incidents import service
from agent.incidents.models import Incident, IncidentPolicy, Receipt


async def enqueue_followup(
    thread_id: str, key: str, text: str, *, delay: float = 0
) -> dict[str, Any]:
    records = await service.INVESTIGATIONS.search(filter={"agent_thread_id": thread_id})
    record = next((item for item in records if not item.expired), None)
    policy = await service.get_policy()
    if record is None or not policy.enabled or record.workspace_id != policy.workspace_id:
        return {"success": False, "error": "Incident is no longer available"}
    receipt_id = service.fingerprint([thread_id, "agent_followup", key])
    existing = await service.RECEIPTS.get(receipt_id)
    if existing:
        return {"success": True, "wakeup_id": receipt_id, "status": "duplicate"}
    pending = [
        item
        for item in await service.RECEIPTS.search_all(filter={"channel_id": record.channel_id})
        if item.kind == "agent_followup"
        and item.id not in record.processed_receipts
        and item.payload.get("thread_id") == thread_id
        and item.payload.get("policy_version") == policy.version
        and item.payload.get("evidence_scope") == record.evidence_scope
    ]
    if delay and len(pending) >= 10:
        return {"success": False, "error": "Incident already has 10 pending followups"}
    now = time.time()
    available_at = now + max(0, delay)
    await service.RECEIPTS.put(
        receipt_id,
        Receipt(
            id=receipt_id,
            workspace_id=record.workspace_id,
            channel_id=record.channel_id,
            kind="agent_followup",
            received_at=now,
            available_at=available_at,
            payload={
                "thread_id": thread_id,
                "policy_version": policy.version,
                "evidence_scope": record.evidence_scope,
                "text": service.redact_context(text)[:8000],
            },
        ),
    )
    await service.wake()
    return {
        "success": True,
        "wakeup_id": receipt_id,
        "scheduled_for": datetime.fromtimestamp(available_at, UTC).isoformat(),
        "thread_id": thread_id,
    }


async def followup_current(receipt: Receipt, record: Incident, policy: IncidentPolicy) -> bool:
    from agent.incidents.runtime import evidence_scope

    return (
        receipt.payload.get("thread_id") == record.agent_thread_id
        and receipt.payload.get("policy_version") == policy.version
        and receipt.payload.get("evidence_scope") == record.evidence_scope
        and record.evidence_scope == await evidence_scope()
    )
