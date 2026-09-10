"""Store-backed requests for workspace automation creation."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import HTTPException
from langgraph_sdk.errors import ConflictError

from agent.dashboard.schedules import ScheduleCreateBody
from agent.store import get_value, now_iso, put_value, search_all_values
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

AUTOMATION_REQUESTS_NAMESPACE: list[str] = ["automation_requests"]
AutomationRequestStatus = Literal["pending", "approved", "denied"]
_REQUEST_LOCK_TTL_MINUTES = 1
_REQUEST_LOCK_RETRY_SECONDS = 0.05
_REQUEST_LOCK_TIMEOUT_SECONDS = 10


@asynccontextmanager
async def automation_request_lock(request_id: str) -> AsyncIterator[None]:
    """Serialize decisions for one automation request."""
    lock_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:automation-request:{request_id}"))
    client = langgraph_client()
    deadline = asyncio.get_running_loop().time() + _REQUEST_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            await client.threads.create(
                thread_id=lock_id,
                if_exists="raise",
                ttl=_REQUEST_LOCK_TTL_MINUTES,
            )
            break
        except ConflictError:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Timed out waiting for the automation request lock") from None
            await asyncio.sleep(_REQUEST_LOCK_RETRY_SECONDS)
    try:
        yield
    finally:
        try:
            await client.threads.delete(lock_id)
        except Exception:
            logger.warning(
                "Failed to release automation request lock",
                extra={"request_id": request_id},
                exc_info=True,
            )


def _automation_name(body: ScheduleCreateBody) -> str:
    return (body.name or body.prompt.strip().splitlines()[0][:80] or "Scheduled agent").strip()


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    raw_automation = record.get("automation")
    automation: dict[str, Any] = raw_automation if isinstance(raw_automation, dict) else {}
    return {
        "id": record.get("id"),
        "status": record.get("status"),
        "requesterName": record.get("requester_name"),
        "requestedBy": record.get("requested_by"),
        "automation": {
            "name": automation.get("name"),
            "prompt": automation.get("prompt"),
            "schedule": automation.get("schedule"),
            "repo": automation.get("repo"),
            "modelId": automation.get("model_id"),
            "effort": automation.get("effort"),
            "slackChannelId": automation.get("slack_channel_id"),
            "slackChannelName": record.get("slack_channel_name"),
            "slackNotificationMode": automation.get("slack_notification_mode"),
            "adminThread": automation.get("admin_thread") is True,
        },
        "createdAt": record.get("created_at"),
        "resolvedAt": record.get("resolved_at"),
        "resolvedBy": record.get("resolved_by"),
        "denialMessage": record.get("denial_message"),
        "automationId": record.get("automation_id"),
    }


async def create_automation_request(
    body: ScheduleCreateBody,
    *,
    requester_login: str,
    requester_email: str | None,
    requester_name: str,
    requester_slack_id: str | None,
    slack_channel_name: str | None,
) -> dict[str, Any]:
    """Add a pending automation request."""
    request_id = str(uuid.uuid4())
    record: dict[str, Any] = {
        "id": request_id,
        "status": "pending",
        "requester_name": requester_name,
        "requested_by": requester_login,
        "requester_email": requester_email,
        "requester_slack_id": requester_slack_id,
        "automation": {**body.model_dump(mode="json"), "name": _automation_name(body)},
        "slack_channel_name": slack_channel_name,
        "created_at": now_iso(),
        "resolved_at": None,
        "resolved_by": None,
        "denial_message": None,
        "automation_id": None,
    }
    await put_value(AUTOMATION_REQUESTS_NAMESPACE, request_id, record)
    return _summary(record)


async def get_automation_request(request_id: str) -> dict[str, Any] | None:
    """Get a raw automation request."""
    return await get_value(AUTOMATION_REQUESTS_NAMESPACE, request_id)


async def list_automation_requests(
    status: AutomationRequestStatus | Literal["all"] = "pending",
) -> list[dict[str, Any]]:
    """List automation requests newest first."""
    records = await search_all_values(AUTOMATION_REQUESTS_NAMESPACE)
    if status != "all":
        records = [record for record in records if record.get("status") == status]
    records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return [_summary(record) for record in records]


async def resolve_automation_request(
    request_id: str,
    *,
    status: Literal["approved", "denied"],
    resolved_by: str,
    automation_id: str | None = None,
    denial_message: str | None = None,
) -> dict[str, Any]:
    """Resolve a pending automation request."""
    record = await get_automation_request(request_id)
    if not record:
        raise HTTPException(404, "automation request not found")
    if record.get("status") != "pending":
        raise HTTPException(409, f"automation request is already {record.get('status')}")
    resolved = {
        **record,
        "status": status,
        "resolved_at": now_iso(),
        "resolved_by": resolved_by,
        "automation_id": automation_id,
        "denial_message": denial_message,
    }
    await put_value(AUTOMATION_REQUESTS_NAMESPACE, request_id, resolved)
    return _summary(resolved)
