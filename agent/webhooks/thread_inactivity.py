"""Emit one inactivity event per quiet period for unresolved public threads."""

import asyncio
import logging

from sqlalchemy import text

from agent.database import transaction
from agent.webhooks.event_log import EventLog, LoggedEvent
from agent.webhooks.event_subscriptions import EventSubscription

logger = logging.getLogger(__name__)
_STOP = asyncio.Event()
_WORKER: asyncio.Task[None] | None = None

_EMIT = text(
    """
    WITH quiet AS (
        SELECT t.thread_id, w.id AS workspace_id, activity.last_activity_at
        FROM thread t
        JOIN workspace w ON w.slug = COALESCE(
            t.metadata->>'workspace', t.metadata->>'environment', 'default')
        CROSS JOIN LATERAL (
            SELECT max(GREATEST(requested_at, started_at, completed_at)) AS last_activity_at
            FROM thread_turn WHERE thread_id = t.thread_id
        ) activity
        WHERE t.status <> 'running'
          AND COALESCE(t.metadata->>'visibility', 'public') = 'public'
          AND COALESCE(t.metadata->>'resolved', 'false') <> 'true'
          AND activity.last_activity_at <= clock_timestamp() - interval '1 hour'
          AND COALESCE(t.metadata->>'inactivity_emitted_for', '')
              <> activity.last_activity_at::text
          AND NOT EXISTS (
              SELECT 1 FROM thread_turn
              WHERE thread_id = t.thread_id AND state IN ('requested', 'running')
          )
        ORDER BY activity.last_activity_at
        LIMIT 100
        FOR UPDATE OF t SKIP LOCKED
    ), claimed AS (
        UPDATE thread t
        SET metadata = jsonb_set(t.metadata, '{inactivity_emitted_for}',
                                to_jsonb(quiet.last_activity_at::text))
        FROM quiet WHERE t.thread_id = quiet.thread_id
        RETURNING t.thread_id, quiet.workspace_id, quiet.last_activity_at
    )
    INSERT INTO event_log (source, endpoint, event_type, delivery_id, payload, workspace_id)
    SELECT 'thread', 'thread-inactivity', 'thread_inactive',
           thread_id || ':' || last_activity_at::text,
           jsonb_build_object('thread_id', thread_id,
                              'last_activity_at', last_activity_at,
                              'inactive_for_seconds', 3600), workspace_id
    FROM claimed
    RETURNING source, event_type, delivery_id, received_at, payload,
              user_id, workspace_id, repository_id, pull_request_id
    """
)


async def emit_inactivity_events() -> int:
    await EventLog.ensure_partitions()
    async with transaction() as conn:
        result = await conn.execute(_EMIT)
        events = [LoggedEvent.model_validate(dict(row)) for row in result.mappings()]
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
            await asyncio.wait_for(_STOP.wait(), timeout=60)
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
