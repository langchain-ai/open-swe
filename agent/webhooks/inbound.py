"""Append-only log of verified GitHub, Slack, and Linear webhook deliveries."""

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qs

from fastapi import Request
from pydantic import JsonValue
from sqlalchemy import text

from agent.database import configured, transaction

logger = logging.getLogger(__name__)

type WebhookSource = Literal["github", "slack", "linear"]

RETAINED_DAYS = 2
_TABLE = "inbound_webhooks"
_ROTATE_INTERVAL_SECONDS = 3600

_STOP = asyncio.Event()
_TASK: asyncio.Task[None] | None = None


class InboundWebhook:
    @classmethod
    async def record(
        cls,
        request: Request,
        body: bytes,
        source: WebhookSource,
        *,
        event_type: str = "",
        delivery_id: str = "",
    ) -> None:
        """Never raises: a delivery that cannot be logged is still handled."""
        if not configured():
            return
        try:
            async with transaction() as conn:
                await conn.execute(
                    text(
                        f"INSERT INTO {_TABLE} (source, endpoint, event_type, delivery_id, payload) "
                        "VALUES (:source, :endpoint, :event_type, :delivery_id, "
                        "CAST(:payload AS jsonb))"
                    ),
                    {
                        "source": source,
                        "endpoint": request.url.path,
                        "event_type": event_type,
                        "delivery_id": delivery_id,
                        "payload": json.dumps(cls._decode(request, body)),
                    },
                )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Recording an inbound webhook failed",
                extra={"webhook_source": source, "webhook_endpoint": request.url.path},
                exc_info=True,
            )

    @classmethod
    async def rotate_partitions(cls, today: date | None = None) -> None:
        """Create today's and tomorrow's partitions and drop those older than the window."""
        today = today or datetime.now(UTC).date()
        oldest = today - timedelta(days=RETAINED_DAYS - 1)
        async with transaction() as conn:
            await conn.execute(text(f"SELECT pg_advisory_xact_lock(hashtext('{_TABLE}'))"))
            for day in (today, today + timedelta(days=1)):
                await conn.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {cls._partition(day)} PARTITION OF {_TABLE} "
                        f"FOR VALUES FROM ('{day.isoformat()} 00:00+00') "
                        f"TO ('{(day + timedelta(days=1)).isoformat()} 00:00+00')"
                    )
                )
            partitions = await conn.execute(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    f"WHERE i.inhparent = '{_TABLE}'::regclass"
                )
            )
            for name in partitions.scalars().all():
                if date.fromisoformat(name.removeprefix(f"{_TABLE}_")) < oldest:
                    await conn.execute(text(f"DROP TABLE {name}"))

    @staticmethod
    def _partition(day: date) -> str:
        return f"{_TABLE}_{day.strftime('%Y%m%d')}"

    @staticmethod
    def _decode(request: Request, body: bytes) -> JsonValue:
        decoded = body.decode("utf-8", errors="replace")
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            return {key: values[-1] for key, values in parse_qs(decoded).items()}
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            return decoded


async def start() -> None:
    """Rotate once before serving, then hourly; a failed rotation is logged and retried."""
    global _TASK
    if not configured() or (_TASK is not None and not _TASK.done()):
        return
    _STOP.clear()
    try:
        await InboundWebhook.rotate_partitions()
    except Exception:  # noqa: BLE001
        logger.warning("Rotating inbound webhook partitions failed", exc_info=True)
    _TASK = asyncio.create_task(_rotate_forever(), name="inbound-webhook-partitions")


async def stop() -> None:
    global _TASK
    _STOP.set()
    if _TASK is not None:
        await _TASK
    _TASK = None


async def _rotate_forever() -> None:
    while True:
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=_ROTATE_INTERVAL_SECONDS)
        except TimeoutError:
            pass
        else:
            return
        try:
            await InboundWebhook.rotate_partitions()
        except Exception:  # noqa: BLE001
            logger.warning("Rotating inbound webhook partitions failed", exc_info=True)
