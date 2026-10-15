"""Turn due thread inactivity deadlines into ``thread_inactive`` event log rows."""

import asyncio
import json
import logging
from datetime import datetime

from pydantic import BaseModel, JsonValue
from sqlalchemy import text

from openswe.database import transaction
from openswe.transcript.inactivity import INACTIVE_AFTER
from openswe.webhooks.event_log import EventLog, LoggedEvent
from openswe.webhooks.event_subscriptions import EventSubscription
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG

logger = logging.getLogger(__name__)
_BATCH = 100
_POLL_SECONDS = 60
_STOP = asyncio.Event()
_WORKER: asyncio.Task[None] | None = None

_CLAIM_DUE = text(
    """
    DELETE FROM thread_inactivity AS due USING thread
    WHERE due.thread_id IN (
        SELECT thread_id FROM thread_inactivity
        WHERE due_at <= clock_timestamp()
        ORDER BY due_at
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    )
      AND thread.thread_id = due.thread_id
    RETURNING due.thread_id, due.last_activity_at, thread.metadata
    """
)

_INSERT = text(
    """
    INSERT INTO event_log (source, endpoint, event_type, delivery_id, payload, workspace_id)
    SELECT 'thread', 'thread-inactivity', 'thread_inactive', :delivery_id,
           CAST(:payload AS jsonb), id
    FROM workspace WHERE slug = :workspace
    RETURNING source, event_type, delivery_id, received_at, payload,
              user_id, workspace_id, repository_id, pull_request_id
    """
)


class _DueThread(BaseModel):
    thread_id: str
    last_activity_at: datetime
    metadata: dict[str, JsonValue]

    @property
    def watchable(self) -> bool:
        return (
            self.metadata.get("visibility", "public") == "public"
            and self.metadata.get("resolved") is not True
        )

    @property
    def workspace(self) -> str:
        for key in ("workspace", "environment"):
            value = self.metadata.get(key)
            if isinstance(value, str) and value:
                return value
        return DEFAULT_WORKSPACE_SLUG


async def emit_inactivity_events() -> int:
    """Emit an event for every due deadline, each exactly once; returns how many."""
    await EventLog.ensure_partitions()
    events: list[LoggedEvent] = []
    async with transaction() as conn:
        claimed = await conn.execute(_CLAIM_DUE, {"limit": _BATCH})
        for due in (_DueThread.model_validate(dict(row)) for row in claimed.mappings()):
            if not due.watchable:
                continue
            payload = {
                "thread_id": due.thread_id,
                "last_activity_at": due.last_activity_at.isoformat(),
                "inactive_for_seconds": int(INACTIVE_AFTER.total_seconds()),
            }
            inserted = await conn.execute(
                _INSERT,
                {
                    "delivery_id": f"{due.thread_id}:{payload['last_activity_at']}",
                    "payload": json.dumps(payload),
                    "workspace": due.workspace,
                },
            )
            events.extend(LoggedEvent.model_validate(dict(row)) for row in inserted.mappings())
    for event in events:
        await EventSubscription.deliver(event)
    return len(events)


async def _run() -> None:
    while not _STOP.is_set():
        try:
            await emit_inactivity_events()
        except Exception:  # noqa: BLE001
            logger.warning("Thread inactivity sweep failed", exc_info=True)
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=_POLL_SECONDS)
        except TimeoutError:
            pass


async def start() -> None:
    global _WORKER
    if _WORKER is not None and not _WORKER.done():
        return
    _STOP.clear()
    _WORKER = asyncio.create_task(_run(), name="thread-inactivity-worker")


async def stop() -> None:
    global _WORKER
    _STOP.set()
    if _WORKER is not None:
        await _WORKER
    _WORKER = None
