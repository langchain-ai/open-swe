"""The reconciler that keeps ``thread_index`` eventually consistent with LangGraph.

Write-through and transcript turn events keep the rows the app itself changes
fresh; this sweep catches everything else. Each tick walks LangGraph threads
newest-first down to the last mark, settles rows still marked running whose run
has ended, and drops rows whose thread LangGraph no longer has. On an empty
table the walk goes all the way down, which is the backfill.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from langgraph_sdk.schema import Thread
from sqlalchemy import text

from agent.database import postgres
from agent.threads.index import (
    ThreadIndexRow,
    delete_thread_index,
    load_thread_index_row,
    upsert_thread_index,
    upsert_thread_index_rows,
)
from agent.threads.listing import settle_review_walkthrough
from agent.threads.summary import _latest_run_info
from agent.utils.json_types import JsonObject, thread_metadata
from agent.utils.thread_ops import langgraph_client, update_thread_metadata

logger = logging.getLogger(__name__)

TASK = "thread_index_sync"
_SCHEDULER_ASSISTANT_ID = "scheduler"
# LangGraph cron schedules are five-field cron expressions, so a minute is the
# shortest interval they express; the design's 30 s target is not reachable.
_SCHEDULE = "* * * * *"

_PAGE_SIZE = 100
# 50k threads. A walk this long is a backfill that outgrew one tick.
_MAX_PAGES = 500
# Covers commit and clock skew between LangGraph's writes and the mark.
_MARK_OVERLAP = timedelta(minutes=5)
_CONCURRENCY = 8
_MAX_RUNNING_ROWS = 500
_DELETION_CHECK_AGE = timedelta(days=7)
_MAX_DELETION_CHECKS = 200
_LIVE_RUN_STATUSES = frozenset({"pending", "running"})
# pg_advisory_lock key: one sweep at a time across every replica and the scheduler.
_LOCK_KEY = 0x7468_7264_6978  # "thrdix"


@dataclass(slots=True)
class ThreadIndexSyncResult:
    scanned: int = 0
    upserted: int = 0
    settled: int = 0
    deleted: int = 0
    pages: int = 0
    full: bool = False
    skipped: bool = False

    def as_json(self) -> JsonObject:
        return asdict(self)


@asynccontextmanager
async def _sync_lock() -> AsyncIterator[bool]:
    """A session-level advisory lock held on its own connection for the whole sweep."""
    async with postgres.engine().connect() as conn:
        acquired = (
            await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY})
        ).scalar_one()
        await conn.commit()
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            try:
                await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})
                await conn.commit()
            except Exception:
                # A pooled connection must not keep the lock; dropping it releases it.
                logger.warning("Could not release the thread index sync lock", exc_info=True)
                await conn.invalidate()


def _langgraph_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _gather_bounded[T](items: Sequence[T], work: Callable[[T], Awaitable[int]]) -> int:
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def bounded(item: T) -> int:
        async with semaphore:
            return await work(item)

    return sum(await asyncio.gather(*(bounded(item) for item in items)))


async def _walk(
    client: LangGraphClient, result: ThreadIndexSyncResult, mark: datetime | None
) -> datetime | None:
    """Upsert LangGraph threads newest-first; returns the newest ``updated_at`` seen.

    Stops at the first page wholly older than the mark, or at the end on a full walk.
    """
    async with postgres.transaction() as conn:
        walk_started = (await conn.execute(text("SELECT clock_timestamp()"))).scalar_one()
    floor = None if result.full or mark is None else mark - _MARK_OVERLAP
    newest: datetime | None = None
    for page in range(_MAX_PAGES):
        threads: list[Thread] = await client.threads.search(
            metadata={},
            limit=_PAGE_SIZE,
            offset=page * _PAGE_SIZE,
            sort_by="updated_at",
            sort_order="desc",
            select=["thread_id", "status", "metadata", "created_at", "updated_at"],
        )
        if not threads:
            return newest
        result.pages += 1
        result.scanned += len(threads)
        result.upserted += await upsert_thread_index_rows(threads, written_before=walk_started)
        stamps = [
            stamp
            for thread in threads
            if (stamp := _langgraph_timestamp(thread.get("updated_at"))) is not None
        ]
        if stamps:
            newest = max(newest or stamps[0], *stamps)
        if len(threads) < _PAGE_SIZE:
            return newest
        if floor is not None and stamps and max(stamps) < floor:
            return newest
    logger.warning(
        "Thread index sync hit its page cap",
        extra={"thread_index_sync": {"pages": _MAX_PAGES, "full": result.full}},
    )
    return newest


def _thread_from_row(row: ThreadIndexRow) -> JsonObject:
    return {
        "thread_id": row.thread_id,
        "status": row.thread_status,
        "metadata": row.metadata,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


async def _settle_running_row(client: LangGraphClient, thread_id: str) -> int:
    """Settle one row marked running whose run, or review walkthrough, has ended."""
    row = await load_thread_index_row(thread_id)
    if row is None or row.status != "running":
        return 0
    try:
        if row.reader_login is not None:
            before = row.metadata.get("walkthrough_state")
            settled = await settle_review_walkthrough(client, _thread_from_row(row))
            if thread_metadata(settled).get("walkthrough_state") != before:
                return 1
        run_status, run_id = await _latest_run_info(client, thread_id)
        if run_status is None or run_status in _LIVE_RUN_STATUSES:
            return 0
        patch: JsonObject = {"latest_run_status": run_status}
        if run_id is not None:
            patch["latest_run_id"] = run_id
        await update_thread_metadata(thread_id, patch, client=client)
    except NotFoundError:
        await delete_thread_index(thread_id)
        return 0
    return 1


async def _settle_running(client: LangGraphClient, result: ThreadIndexSyncResult) -> None:
    async with postgres.transaction() as conn:
        thread_ids: list[str] = list(
            (
                await conn.execute(
                    text(
                        "SELECT thread_id FROM thread_index WHERE status = 'running' LIMIT :limit"
                    ),
                    {"limit": _MAX_RUNNING_ROWS},
                )
            )
            .scalars()
            .all()
        )

    async def settle(thread_id: str) -> int:
        try:
            return await _settle_running_row(client, thread_id)
        except Exception:
            logger.warning(
                "Could not settle a running thread index row",
                exc_info=True,
                extra={"thread_index": {"thread_id": thread_id}},
            )
            return 0

    result.settled += await _gather_bounded(thread_ids, settle)


async def _drop_deleted(client: LangGraphClient, result: ThreadIndexSyncResult) -> None:
    """Check rows the walk has not touched in a while; drop those LangGraph lost.

    A thread that still exists is re-upserted, which moves it out of this check
    for another ``_DELETION_CHECK_AGE``.
    """
    async with postgres.transaction() as conn:
        thread_ids: list[str] = list(
            (
                await conn.execute(
                    text(
                        """
                        SELECT thread_id FROM thread_index
                        WHERE synced_at < clock_timestamp() - CAST(:age AS interval)
                        ORDER BY synced_at
                        LIMIT :limit
                        """
                    ),
                    {"age": _DELETION_CHECK_AGE, "limit": _MAX_DELETION_CHECKS},
                )
            )
            .scalars()
            .all()
        )

    async def check(thread_id: str) -> int:
        try:
            thread = await client.threads.get(thread_id)
        except NotFoundError:
            return 1 if await delete_thread_index(thread_id) else 0
        except Exception:
            logger.warning(
                "Could not check whether an indexed thread still exists",
                exc_info=True,
                extra={"thread_index": {"thread_id": thread_id}},
            )
            return 0
        await upsert_thread_index(thread)
        return 0

    result.deleted += await _gather_bounded(thread_ids, check)


async def _load_mark() -> tuple[datetime | None, bool]:
    """The walk's high-water mark, and whether the index is empty."""
    async with postgres.transaction() as conn:
        mark = (
            await conn.execute(
                text("SELECT last_langgraph_updated_at FROM thread_index_sync_state WHERE id = 1")
            )
        ).scalar_one_or_none()
        empty = (
            await conn.execute(text("SELECT NOT EXISTS (SELECT 1 FROM thread_index)"))
        ).scalar_one()
    return mark, empty


async def _save_mark(newest: datetime | None, *, full: bool) -> None:
    async with postgres.transaction() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO thread_index_sync_state (
                    id, last_langgraph_updated_at, last_full_sync_at
                )
                VALUES (
                    1, :newest, CASE WHEN :full THEN clock_timestamp() END
                )
                ON CONFLICT (id) DO UPDATE SET
                    last_langgraph_updated_at = GREATEST(
                        thread_index_sync_state.last_langgraph_updated_at,
                        EXCLUDED.last_langgraph_updated_at
                    ),
                    last_full_sync_at = COALESCE(
                        EXCLUDED.last_full_sync_at, thread_index_sync_state.last_full_sync_at
                    )
                """
            ),
            {"newest": newest, "full": full},
        )


async def _sync(client: LangGraphClient, *, full: bool) -> ThreadIndexSyncResult:
    mark, empty = await _load_mark()
    result = ThreadIndexSyncResult(full=full or empty or mark is None)
    newest = await _walk(client, result, mark)
    await _save_mark(newest, full=result.full)
    await _settle_running(client, result)
    await _drop_deleted(client, result)
    logger.info("Thread index synced", extra={"thread_index_sync": result.as_json()})
    return result


async def sync_thread_index(*, full: bool = False) -> ThreadIndexSyncResult:
    """One reconciler tick; ``full`` walks every LangGraph thread (the backfill).

    Skipped when PostgreSQL is not configured or another sweep holds the lock.
    """
    if not postgres.configured():
        return ThreadIndexSyncResult(skipped=True)
    async with _sync_lock() as acquired:
        if not acquired:
            return ThreadIndexSyncResult(skipped=True)
        return await _sync(langgraph_client(), full=full)


async def _ensure_sync_cron(client: LangGraphClient) -> None:
    existing = await client.crons.search(
        assistant_id=_SCHEDULER_ASSISTANT_ID, metadata={"kind": TASK}, limit=1
    )
    if existing:
        return
    await client.crons.create(
        _SCHEDULER_ASSISTANT_ID,
        schedule=_SCHEDULE,
        input={"task": TASK},
        metadata={"kind": TASK},
    )
    logger.info("Registered the thread index sync cron", extra={"cron_schedule": _SCHEDULE})


async def bootstrap_thread_index() -> None:
    """At startup: register the sync cron, then run a tick so an empty index backfills now.

    Both happen under the sweep's lock, so replicas starting together neither
    register the cron twice nor walk LangGraph side by side.
    """
    if not postgres.configured():
        return
    try:
        async with _sync_lock() as acquired:
            if not acquired:
                return
            client = langgraph_client()
            try:
                await _ensure_sync_cron(client)
            except Exception:
                logger.warning("Could not register the thread index sync cron", exc_info=True)
            await _sync(client, full=False)
    except Exception:
        logger.exception("Thread index bootstrap failed")
