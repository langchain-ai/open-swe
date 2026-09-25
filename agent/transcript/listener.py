"""Waking SSE subscribers when a thread's log grows.

Correctness comes from Postgres: ``append`` issues ``pg_notify`` inside its
transaction, and one dedicated asyncpg connection per process LISTENs on that
channel and hands the notified version to whoever subscribed to the thread. The
notification carries ids only, so a subscriber always reads the rows itself.

``append`` also publishes in-process. That is an optimisation — the graph and
the HTTP app share a process, so the common case does not have to wait for the
round trip — and it is deliberately the same code path as a notification, so a
missing in-process publish only ever costs latency.

The same connection also carries ``agent.threads.changes`` notifications, which
wake live sidebars rather than transcript readers.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import asyncpg
from sqlalchemy import ARRAY, Text, bindparam, make_url, text

from agent.database import postgres
from agent.threads import changes

logger = logging.getLogger(__name__)

CHANNEL = "open_swe_thread_events"
"""LISTEN/NOTIFY channel carrying ``<thread_id>:<version>`` — ids only, never content."""

DELETED = "deleted"
"""Notification payload suffix for a thread whose transcript was deleted."""

DELETED_VERSION = -1
"""The version a subscriber receives when the thread it follows is deleted."""

_QUEUE_LIMIT = 256
_RECONNECT_DELAY_SECONDS = 0.5
_MAX_RECONNECT_DELAY_SECONDS = 30.0
_HEALTH_CHECK_SECONDS = 5.0

_SUBSCRIBERS: dict[str, set[asyncio.Queue[int]]] = {}
_TASK: asyncio.Task[None] | None = None
_STOP = asyncio.Event()


class _Versions:
    """The versions handed to one subscriber, in the order they arrived."""

    __slots__ = ("_queue",)

    def __init__(self, queue: asyncio.Queue[int]) -> None:
        self._queue = queue

    def __aiter__(self) -> _Versions:
        return self

    async def __anext__(self) -> int:
        return await self._queue.get()


@contextlib.asynccontextmanager
async def subscribe(thread_id: str) -> AsyncIterator[AsyncIterator[int]]:
    """Versions notified for ``thread_id``, as they arrive, for the body of the ``with``.

    Registration happens on entry, so a caller can subscribe and then read its
    replay range without a window in which events are lost. Leaving the block
    unregisters, whether or not the versions were ever awaited: a subscriber
    that gives up during its replay must not leave its queue behind.
    """
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
    _SUBSCRIBERS.setdefault(thread_id, set()).add(queue)
    try:
        yield _Versions(queue)
    finally:
        queues = _SUBSCRIBERS.get(thread_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                _SUBSCRIBERS.pop(thread_id, None)


def publish(thread_id: str, version: int) -> None:
    """Hand ``version`` to this process's subscribers for ``thread_id``.

    A full queue gives up its oldest entry rather than this one. A subscriber
    reads rows by version, so an older notification it never sees costs it
    nothing — but the newest, and ``DELETED_VERSION`` above all, has to arrive
    or it waits on an append that may never come.
    """
    for queue in tuple(_SUBSCRIBERS.get(thread_id, ())):
        try:
            queue.put_nowait(version)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(version)


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
    if thread_id and version == DELETED:
        publish(thread_id, DELETED_VERSION)
        return
    if not thread_id or not version.isdigit():
        logger.warning(
            "Ignored an unreadable transcript notification",
            extra={"transcript": {"notification": payload}},
        )
        return
    publish(thread_id, int(version))


def _on_thread_changed(
    _connection: object, _pid: int, _channel: str, payload: str
) -> None:  # pragma: no cover - driven by Postgres
    if payload:
        changes.publish_local(payload)


async def _resync_subscribers() -> None:
    """Publish each subscribed thread's head after a gap in the notifications.

    While the listener was disconnected, an append in another process notified
    nobody here. A subscriber reads rows by version, so handing it the current
    head is enough to carry it past everything it missed.

    A subscribed thread with no row at all lost its ``deleted`` notification
    the same way, and would otherwise sit on heartbeats forever: a subscriber
    registers before it replays, so a missing head means the thread is gone
    rather than not yet written.
    """
    thread_ids = tuple(_SUBSCRIBERS)
    if not thread_ids:
        return
    async with postgres.read_only_transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT thread_id, version FROM thread WHERE thread_id = ANY(:thread_ids)"
            ).bindparams(bindparam("thread_ids", type_=ARRAY(Text))),
            {"thread_ids": list(thread_ids)},
        )
        heads = {row.thread_id: row.version for row in rows}
    for thread_id in thread_ids:
        publish(thread_id, heads.get(thread_id, DELETED_VERSION))


async def _listen_forever() -> None:
    delay = _RECONNECT_DELAY_SECONDS
    while not _STOP.is_set():
        connection: asyncpg.Connection | None = None
        try:
            connection = await asyncpg.connect(dsn=_dsn())
            await connection.add_listener(CHANNEL, _on_notify)
            await connection.add_listener(changes.CHANNEL, _on_thread_changed)
            delay = _RECONNECT_DELAY_SECONDS
            logger.info("Transcript listener connected", extra={"transcript_channel": CHANNEL})
            # Thread changes notified while disconnected reached nobody here.
            changes.publish_local(changes.RESYNC)
            try:
                await _resync_subscribers()
            except Exception:  # noqa: BLE001
                # A failed catch-up costs a live reader its latency, not its
                # correctness, and must not tear down a healthy listener.
                logger.warning("Could not resync transcript subscribers", exc_info=True)
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
