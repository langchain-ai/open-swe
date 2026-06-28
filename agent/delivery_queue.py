"""Provider-neutral delivery queue records backed by the LangGraph Store."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from .utils.thread_ops import langgraph_client

DELIVERY_QUEUE_NAMESPACE: list[str] = ["delivery_queue"]
DELIVERY_QUEUE_STATUSES = {
    "not-ready",
    "queued",
    "blocked",
    "running",
    "review",
    "done",
    "paused",
    "failed",
}
ACTIVE_DELIVERY_QUEUE_STATUSES = {"queued", "running", "review"}

DeliveryQueueStatus = Literal[
    "not-ready",
    "queued",
    "blocked",
    "running",
    "review",
    "done",
    "paused",
    "failed",
]


class PreflightInput(TypedDict):
    active_project: bool
    readiness: bool
    issue_context: bool
    credentials: bool
    ai_hub_ready: bool
    sandbox_profile: bool
    budget: bool
    duplicate_active_run: bool
    kill_switch: bool


class PreflightBlocker(TypedDict):
    code: str
    message: str


class PreflightResult(TypedDict):
    ready: bool
    blockers: list[PreflightBlocker]


Poller = Callable[[], Awaitable[Iterable[Mapping[str, Any]] | None] | Iterable[Mapping[str, Any]] | None]

_BLOCKER_MESSAGES: dict[str, str] = {
    "active_project": "Project is not active.",
    "readiness": "Work item is not ready for delivery.",
    "issue_context": "Issue context is missing.",
    "credentials": "Required credentials are unavailable.",
    "ai_hub_ready": "AI Hub is not ready.",
    "sandbox_profile": "Sandbox profile is unavailable.",
    "budget": "Delivery budget is unavailable.",
    "duplicate_active_run": "Another active run already exists for this work item.",
    "kill_switch": "Delivery queue kill switch is enabled.",
}
_DEFAULT_PREFLIGHT: PreflightInput = {
    "active_project": True,
    "readiness": True,
    "issue_context": True,
    "credentials": True,
    "ai_hub_ready": True,
    "sandbox_profile": True,
    "budget": True,
    "duplicate_active_run": False,
    "kill_switch": False,
}


def _client():
    return langgraph_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_delivery_queue_dedupe_key(
    project_id: str,
    provider: str,
    external_work_item_id: str,
) -> str:
    return f"{project_id.strip()}:{provider.strip().lower()}:{external_work_item_id.strip()}"


def evaluate_start_preflight(
    *,
    active_project: bool,
    readiness: bool,
    issue_context: bool,
    credentials: bool,
    ai_hub_ready: bool,
    sandbox_profile: bool,
    budget: bool,
    duplicate_active_run: bool,
    kill_switch: bool,
) -> PreflightResult:
    blockers: list[PreflightBlocker] = []
    failing_checks = {
        "active_project": not active_project,
        "readiness": not readiness,
        "issue_context": not issue_context,
        "credentials": not credentials,
        "ai_hub_ready": not ai_hub_ready,
        "sandbox_profile": not sandbox_profile,
        "budget": not budget,
        "duplicate_active_run": duplicate_active_run,
        "kill_switch": kill_switch,
    }
    for code, blocked in failing_checks.items():
        if blocked:
            blockers.append({"code": code, "message": _BLOCKER_MESSAGES[code]})
    return {"ready": not blockers, "blockers": blockers}


def _value_from_item(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _validate_status(status: str) -> DeliveryQueueStatus:
    if status not in DELIVERY_QUEUE_STATUSES:
        raise ValueError(f"unsupported delivery queue status: {status}")
    return status  # type: ignore[return-value]


def _preflight_from_payload(
    payload: Mapping[str, Any], preflight: PreflightInput | None
) -> PreflightInput:
    if preflight is not None:
        return preflight
    value = payload.get("preflight")
    if isinstance(value, dict):
        return {**_DEFAULT_PREFLIGHT, **value}  # type: ignore[return-value]
    return _DEFAULT_PREFLIGHT


def _status_for_upsert(
    payload: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    preflight: PreflightResult,
) -> DeliveryQueueStatus:
    requested_status = payload.get("status")
    if isinstance(requested_status, str):
        return _validate_status(requested_status)
    existing_status = existing.get("status") if existing else None
    if preflight["ready"]:
        if existing_status in {"running", "review", "done", "failed"}:
            return _validate_status(str(existing_status))
        return "queued"
    if existing_status in ACTIVE_DELIVERY_QUEUE_STATUSES:
        return "paused"
    return "not-ready"


async def read_delivery_queue_item(item_id: str) -> dict[str, Any] | None:
    return _value_from_item(await _client().store.get_item(DELIVERY_QUEUE_NAMESPACE, item_id))


async def _put_delivery_queue_item(record: dict[str, Any]) -> dict[str, Any]:
    await _client().store.put_item(DELIVERY_QUEUE_NAMESPACE, record["id"], record)
    return record


async def list_delivery_queue_items(filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    limit = 100
    offset = 0
    while True:
        result = await _client().store.search_items(
            DELIVERY_QUEUE_NAMESPACE,
            filter=filter,
            limit=limit,
            offset=offset,
        )
        items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
        if not items:
            break
        for item in items:
            value = _value_from_item(item)
            if value is not None:
                records.append(value)
        if len(items) < limit:
            break
        offset += len(items)
    records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return records


search_delivery_queue_items = list_delivery_queue_items


async def upsert_delivery_queue_item(
    payload: Mapping[str, Any],
    *,
    preflight: PreflightInput | None = None,
) -> dict[str, Any]:
    project_id = _required_text(payload, "project_id")
    provider = _required_text(payload, "provider").lower()
    external_work_item_id = _required_text(payload, "external_work_item_id")
    dedupe_key = build_delivery_queue_dedupe_key(project_id, provider, external_work_item_id)
    existing = await read_delivery_queue_item(dedupe_key)
    preflight_result = evaluate_start_preflight(**_preflight_from_payload(payload, preflight))
    status = _status_for_upsert(payload, existing, preflight_result)
    now = _now_iso()

    record: dict[str, Any] = {
        **(existing or {}),
        **dict(payload),
        "id": dedupe_key,
        "dedupe_key": dedupe_key,
        "project_id": project_id,
        "provider": provider,
        "external_work_item_id": external_work_item_id,
        "status": status,
        "preflight": preflight_result,
        "blockers": preflight_result["blockers"],
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    return await _put_delivery_queue_item(record)


async def transition_delivery_queue_status(
    item_id: str,
    status: DeliveryQueueStatus,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    record = await read_delivery_queue_item(item_id)
    if record is None:
        raise KeyError(f"delivery queue item not found: {item_id}")
    previous_status = record.get("status")
    updated = {
        **record,
        "previous_status": previous_status,
        "status": _validate_status(status),
        "status_reason": reason,
        "updated_at": _now_iso(),
    }
    return await _put_delivery_queue_item(updated)


async def delivery_queue_poll(poller: Poller | None = None) -> dict[str, Any]:
    if poller is None:
        return {"status": "idle", "items": 0}
    result = poller()
    items = await result if inspect.isawaitable(result) else result
    count = 0
    for item in items or []:
        await upsert_delivery_queue_item(item)
        count += 1
    return {"status": "polled", "items": count}
