"""One LISTEN connection per process, shared by every ``pg_notify`` channel.

Each channel registers a synchronous handler for payloads and, optionally, a
coroutine run after every (re)connect. Notifications sent while the connection
was down are gone, so ``on_connected`` is where a channel catches up: it is
handed how long the connection was down (``None`` on the first connect).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg
from sqlalchemy import make_url

from agent.database import postgres

logger = logging.getLogger(__name__)

type NotifyHandler = Callable[[str], None]
type ConnectedHandler = Callable[[float | None], Awaitable[None]]

_RECONNECT_DELAY_SECONDS = 0.5
_MAX_RECONNECT_DELAY_SECONDS = 30.0
_HEALTH_CHECK_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class _Channel:
    on_notify: NotifyHandler
    on_connected: ConnectedHandler | None


_CHANNELS: dict[str, _Channel] = {}
_CONNECTION: asyncpg.Connection | None = None
_TASK: asyncio.Task[None] | None = None
_STOP = asyncio.Event()


async def listen(
    channel: str, on_notify: NotifyHandler, on_connected: ConnectedHandler | None = None
) -> None:
    """Route ``channel``'s payloads to ``on_notify``, joining a live connection at once."""
    _CHANNELS[channel] = _Channel(on_notify, on_connected)
    connection = _CONNECTION
    if connection is not None and not connection.is_closed():
        await connection.add_listener(channel, _dispatch)


def unlisten(channel: str) -> None:
    _CHANNELS.pop(channel, None)


def start() -> None:
    """Begin listening. A database that cannot be reached is logged, not raised."""
    global _TASK
    if not postgres.configured():
        logger.info("PostgreSQL listener disabled: PostgreSQL is not configured")
        return
    if _TASK is not None and not _TASK.done():
        return
    _STOP.clear()
    _TASK = asyncio.create_task(_listen_forever(), name="postgres-listener")


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
    _CHANNELS.clear()


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


def _dispatch(
    _connection: object, _pid: int, channel: str, payload: str
) -> None:  # pragma: no cover - driven by Postgres
    registered = _CHANNELS.get(channel)
    if registered is None:
        return
    try:
        registered.on_notify(payload)
    except Exception:  # noqa: BLE001
        # One channel's bad payload must not tear down the connection every
        # other channel shares.
        logger.warning(
            "A PostgreSQL notification handler failed",
            extra={"pg_channel": channel},
            exc_info=True,
        )


async def _catch_up(down_seconds: float | None) -> None:
    for channel, registered in tuple(_CHANNELS.items()):
        if registered.on_connected is None:
            continue
        try:
            await registered.on_connected(down_seconds)
        except Exception:  # noqa: BLE001
            # A failed catch-up costs that channel's readers latency, not the
            # connection every other channel is using.
            logger.warning(
                "Catching up a PostgreSQL channel failed",
                extra={"pg_channel": channel},
                exc_info=True,
            )


async def _listen_forever() -> None:
    global _CONNECTION
    delay = _RECONNECT_DELAY_SECONDS
    down_since: float | None = None
    while not _STOP.is_set():
        connection: asyncpg.Connection | None = None
        try:
            connection = await asyncpg.connect(dsn=_dsn())
            for channel in tuple(_CHANNELS):
                await connection.add_listener(channel, _dispatch)
            _CONNECTION = connection
            delay = _RECONNECT_DELAY_SECONDS
            logger.info("PostgreSQL listener connected", extra={"pg_channels": list(_CHANNELS)})
            await _catch_up(None if down_since is None else time.monotonic() - down_since)
            while not _STOP.is_set() and not connection.is_closed():
                try:
                    await asyncio.wait_for(_STOP.wait(), timeout=_HEALTH_CHECK_SECONDS)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Readers in this process keep working off in-process publishes;
            # what goes quiet is a change made by another replica.
            logger.warning("PostgreSQL listener lost its connection", exc_info=True)
        finally:
            _CONNECTION = None
            if down_since is None or connection is not None:
                down_since = time.monotonic()
            if connection is not None and not connection.is_closed():
                try:
                    await connection.close()
                except Exception:  # noqa: BLE001
                    logger.warning("Closing the PostgreSQL listener failed", exc_info=True)
        if _STOP.is_set():
            return
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=delay)
        except TimeoutError:
            delay = min(delay * 2, _MAX_RECONNECT_DELAY_SECONDS)
