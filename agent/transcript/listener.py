"""Waking SSE subscribers when a thread's log grows.

Correctness comes from Postgres: ``append`` issues ``pg_notify`` inside its
transaction, and one dedicated asyncpg connection per process LISTENs on that
channel and hands the notified version to whoever subscribed to the thread. The
notification carries ids only, so a subscriber always reads the rows itself.

``append`` also publishes in-process. That is an optimisation — the graph and
the HTTP app share a process, so the common case does not have to wait for the
round trip — and it is deliberately the same code path as a notification, so a
missing in-process publish only ever costs latency.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator

import asyncpg
from sqlalchemy import make_url

from agent.database import postgres

logger = logging.getLogger(__name__)

CHANNEL = "open_swe_thread_events"
_QUEUE_LIMIT = 256
_RECONNECT_DELAY_SECONDS = 0.5
_MAX_RECONNECT_DELAY_SECONDS = 30.0
_HEALTH_CHECK_SECONDS = 5.0

_SUBSCRIBERS: dict[str, set[asyncio.Queue[int]]] = {}
_TASK: asyncio.Task[None] | None = None
_STOP = asyncio.Event()


def subscribe(thread_id: str) -> AsyncGenerator[int]:
    """Versions notified for ``thread_id``, newest first as they arrive.

    Registration happens before this returns, so a caller can subscribe and
    then read its replay range without a window in which events are lost. The
    iterator must be closed (``aclosing``) to unregister.
    """
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
    _SUBSCRIBERS.setdefault(thread_id, set()).add(queue)

    async def _versions() -> AsyncGenerator[int]:
        try:
            while True:
                yield await queue.get()
        finally:
            queues = _SUBSCRIBERS.get(thread_id)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    _SUBSCRIBERS.pop(thread_id, None)

    return _versions()


def publish(thread_id: str, version: int) -> None:
    """Hand ``version`` to this process's subscribers for ``thread_id``."""
    for queue in tuple(_SUBSCRIBERS.get(thread_id, ())):
        try:
            queue.put_nowait(version)
        except asyncio.QueueFull:
            # The subscriber is behind; it reads rows by version, so the next
            # notification it does receive carries it past everything it missed.
            logger.warning(
                "Dropped a transcript notification for a slow subscriber",
                extra={"transcript": {"thread_id": thread_id, "version": version}},
            )


async def start() -> None:
    """Begin listening. A database that cannot be reached is logged, not raised."""
    global _TASK
    if not postgres.configured():
        logger.info("Transcript listener disabled: PostgreSQL is not configured")
        return
    if _TASK is not None and not _TASK.done():
        return
    _STOP.clear()
    _TASK = asyncio.create_task(_listen_forever(), name="transcript-listener")


async def stop() -> None:
    global _TASK
    _STOP.set()
    task = _TASK
    _TASK = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _SUBSCRIBERS.clear()


def _dsn() -> str:
    """The engine's URI as libpq spells it: no driver prefix, ``sslmode`` not ``ssl``."""
    uri = postgres.uri()
    if uri is None:
        raise RuntimeError("PostgreSQL is not configured")
    url = make_url(uri).set(drivername="postgresql")
    query = dict(url.query)
    ssl = query.pop("ssl", None)
    if ssl is not None:
        query["sslmode"] = ssl
    return url.set(query=query).render_as_string(hide_password=False)


def _on_notify(
    _connection: object, _pid: int, _channel: str, payload: str
) -> None:  # pragma: no cover - driven by Postgres
    thread_id, _, version = payload.rpartition(":")
    if not thread_id or not version.isdigit():
        logger.warning(
            "Ignored an unreadable transcript notification",
            extra={"transcript": {"notification": payload}},
        )
        return
    publish(thread_id, int(version))


async def _listen_forever() -> None:
    delay = _RECONNECT_DELAY_SECONDS
    while not _STOP.is_set():
        connection: asyncpg.Connection | None = None
        try:
            connection = await asyncpg.connect(dsn=_dsn())
            await connection.add_listener(CHANNEL, _on_notify)
            delay = _RECONNECT_DELAY_SECONDS
            logger.info("Transcript listener connected", extra={"transcript_channel": CHANNEL})
            while not _STOP.is_set() and not connection.is_closed():
                try:
                    await asyncio.wait_for(_STOP.wait(), timeout=_HEALTH_CHECK_SECONDS)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Subscribers keep working off in-process publishes while this
            # reconnects; a live thread in another process is what goes quiet.
            logger.warning("Transcript listener lost its connection", exc_info=True)
        finally:
            if connection is not None and not connection.is_closed():
                try:
                    await connection.close()
                except Exception:  # noqa: BLE001
                    logger.warning("Closing the transcript listener failed", exc_info=True)
        if _STOP.is_set():
            return
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=delay)
        except TimeoutError:
            delay = min(delay * 2, _MAX_RECONNECT_DELAY_SECONDS)
