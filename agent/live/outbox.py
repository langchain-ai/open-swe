"""Recording what changed, and reading back what a reconnecting reader missed.

A write names the topics it changed inside its own transaction: one
``live_event`` row per topic, plus a ``pg_notify`` that Postgres delivers only
if the transaction commits. Live readers hear the notification; the rows exist
so a reader that was disconnected can ask which of its topics changed while it
was gone.
"""

import logging
from collections.abc import Iterable, Iterator, Mapping
from datetime import timedelta

from sqlalchemy import ARRAY, Float, Text, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from agent.database import postgres

logger = logging.getLogger(__name__)

CHANNEL = "open_swe_live"
"""LISTEN/NOTIFY channel carrying newline-separated topics."""

RETENTION = timedelta(days=1)
"""How far back a reconnecting reader can be told what it missed."""

_NOTIFY_PAYLOAD_LIMIT = 7900

_INSERT = text("INSERT INTO live_event (topic) SELECT unnest(:topics)").bindparams(
    bindparam("topics", type_=ARRAY(Text))
)
_NOTIFY = text("SELECT pg_notify(:channel, :payload)")
_CHANGED_SINCE = text(
    """
    SELECT DISTINCT wanted.topic
    FROM unnest(:topics, :ages) AS wanted (topic, age)
    JOIN live_event ON live_event.topic = wanted.topic
    WHERE live_event.created_at >= clock_timestamp() - make_interval(secs => wanted.age)
    """
).bindparams(bindparam("topics", type_=ARRAY(Text)), bindparam("ages", type_=ARRAY(Float)))
_PRUNE = text(
    "DELETE FROM live_event WHERE created_at < clock_timestamp() - make_interval(secs => :seconds)"
).bindparams(bindparam("seconds", type_=Float))


async def publish(conn: AsyncConnection | AsyncSession, *topics: str) -> None:
    """Mark ``topics`` changed when ``conn``'s transaction commits.

    Call it last in the transaction: a replay window is measured from the row's
    insert, so a transaction that stays open long after publishing can commit a
    change older than the window a reconnecting reader asks about.
    """
    unique = sorted(set(topics))
    if not unique:
        return
    await conn.execute(_INSERT, {"topics": unique})
    for payload in _payloads(unique):
        await conn.execute(_NOTIFY, {"channel": CHANNEL, "payload": payload})


async def publish_standalone(*topics: str) -> None:
    """Mark ``topics`` changed after a write Postgres had no part in, such as the Store.

    Never raises: the write it follows has already happened, and a reader that
    misses this only waits for its next refetch.
    """
    if not postgres.configured():
        return
    try:
        async with postgres.transaction() as conn:
            await publish(conn, *topics)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Publishing a live change failed", extra={"live_topics": list(topics)}, exc_info=True
        )


async def changed_since(ages: Mapping[str, float]) -> set[str]:
    """The topics in ``ages`` that changed within their age, in seconds.

    A topic whose age reaches past retention is reported as changed: nothing
    can say it did not.
    """
    if not ages:
        return set()
    horizon = RETENTION.total_seconds()
    expired = {topic for topic, age in ages.items() if age >= horizon}
    recent = {topic: age for topic, age in ages.items() if topic not in expired}
    if not recent:
        return expired
    async with postgres.connection() as conn:
        rows = await conn.execute(
            _CHANGED_SINCE, {"topics": list(recent), "ages": list(recent.values())}
        )
        return expired | {row.topic for row in rows}


async def prune() -> int:
    async with postgres.transaction() as conn:
        result = await conn.execute(_PRUNE, {"seconds": RETENTION.total_seconds()})
        return result.rowcount


def _payloads(topics: Iterable[str]) -> Iterator[str]:
    """Newline-joined topics, split so no payload passes Postgres's 8000-byte limit."""
    batch: list[str] = []
    size = 0
    for topic in topics:
        length = len(topic.encode()) + 1
        if batch and size + length > _NOTIFY_PAYLOAD_LIMIT:
            yield "\n".join(batch)
            batch, size = [], 0
        batch.append(topic)
        size += length
    if batch:
        yield "\n".join(batch)
