"""The ``thread_index`` read model: one row per LangGraph thread the sidebar could list.

Every column is derived with the same readers the Python list predicates use,
so a query against the index can never disagree with the filtering it replaces.
The row is a projection: it can always be rebuilt from the LangGraph thread.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import ARRAY, Text, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres
from agent.review.session import ReviewSessionMetadata
from agent.threads.summary import (
    _SURFACED_SOURCES,
    _is_thread_resolved,
    _is_thread_viewed,
    _metadata_repo,
    _metadata_string,
    _thread_classification,
    _thread_id,
    _thread_run_id,
    _thread_timestamp_ms,
    review_chat_status,
    run_status_to_agent_status,
    thread_is_private,
    thread_is_unlisted,
    thread_source,
)
from agent.utils.json_types import JsonObject, ThreadLike, thread_metadata
from agent.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    participant_logins,
)

logger = logging.getLogger(__name__)

type ThreadVisibility = Literal["public", "private"]
type ThreadIndexStatus = Literal["idle", "running", "finished", "error", "interrupted"]

_INCIDENTS_SOURCE = "incidents_agent"
# Threads created before participants existed carry only these keys.
_LEGACY_PARTICIPANT_KEYS: tuple[str, ...] = ("github_login", "triggering_user_email")


@dataclass(frozen=True, slots=True)
class ThreadIndexRow:
    thread_id: str
    listed: bool
    participants: list[str]
    visibility: ThreadVisibility
    owner_login: str | None
    reader_login: str | None
    admin_thread: bool
    category: str
    source: str
    schedule_id: str | None
    repo_full_name: str | None
    workspace: str | None
    status: str
    latest_run_id: str | None
    resolved: bool
    viewed: bool
    title: str | None
    created_at: datetime
    updated_at: datetime
    thread_status: str
    metadata: JsonObject


def _lowered(value: object) -> str | None:
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _participants(metadata: Mapping[str, object], reader_login: str | None) -> list[str]:
    principals = {
        *participant_logins(metadata.get(PARTICIPANT_LOGINS_KEY)),
        *participant_logins(metadata.get(PARTICIPANT_EMAILS_KEY)),
    }
    principals.update(
        login for key in _LEGACY_PARTICIPANT_KEYS if (login := _lowered(metadata.get(key)))
    )
    if reader_login:
        principals.add(reader_login)
    return sorted(principals)


def is_index_candidate(thread: ThreadLike) -> bool:
    """Whether the app ever stamped this thread as one a person could see.

    Lock and claim threads, and the threads each cron tick runs on, carry none
    of these keys; indexing them would only leave rows behind once they vanish.
    """
    metadata = thread_metadata(thread)
    return any(
        metadata.get(key)
        for key in (
            "source",
            PARTICIPANT_LOGINS_KEY,
            PARTICIPANT_EMAILS_KEY,
            *_LEGACY_PARTICIPANT_KEYS,
        )
    )


def _from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, UTC)


def derive_thread_index_row(thread: ThreadLike) -> ThreadIndexRow:
    """The index row for a LangGraph thread ``{thread_id, status, metadata, *_at}``."""
    thread_id = _thread_id(thread)
    if thread_id is None:
        raise ValueError("thread has no thread_id")
    metadata = thread_metadata(thread)
    source = thread_source(metadata)
    review = ReviewSessionMetadata.parse(metadata)
    reader_login = _lowered(review.github_login) if review is not None else None
    category, _, _ = _thread_classification(metadata)
    raw_thread_status = thread.get("status")
    thread_status = raw_thread_status if isinstance(raw_thread_status, str) else "idle"
    run_status = metadata.get("latest_run_status")
    status = run_status_to_agent_status(
        thread_status, run_status if isinstance(run_status, str) else None
    )
    viewed = _is_thread_viewed(metadata, None)
    if review is not None:
        status, viewed = review_chat_status(review, status, viewed)
    repo_full_name = _metadata_repo(metadata)[2]
    return ThreadIndexRow(
        thread_id=thread_id,
        listed=(source in _SURFACED_SOURCES or review is not None)
        and not thread_is_unlisted(metadata)
        and source != _INCIDENTS_SOURCE,
        participants=_participants(metadata, reader_login),
        visibility="private" if thread_is_private(metadata) else "public",
        owner_login=_lowered(metadata.get("owner_login")),
        reader_login=reader_login,
        admin_thread=metadata.get("admin_thread") is True,
        category=category,
        source=source,
        schedule_id=_metadata_string(metadata, "schedule_id"),
        repo_full_name=repo_full_name.lower() or None,
        workspace=_metadata_string(metadata, "workspace")
        or _metadata_string(metadata, "environment"),
        status=status,
        latest_run_id=_thread_run_id(metadata, None),
        resolved=_is_thread_resolved(metadata),
        viewed=viewed,
        title=_metadata_string(metadata, "title"),
        created_at=_from_ms(_thread_timestamp_ms(thread, "created_at")),
        updated_at=_from_ms(_thread_timestamp_ms(thread, "updated_at")),
        thread_status=thread_status,
        metadata=dict(metadata),
    )


_UPSERT_SQL = """
    INSERT INTO thread_index (
        thread_id, listed, participants, visibility, owner_login, reader_login, admin_thread,
        category, source, schedule_id, repo_full_name, workspace, status, latest_run_id,
        resolved, viewed, title, created_at, updated_at, thread_status, metadata
    )
    VALUES (
        :thread_id, :listed, :participants, :visibility, :owner_login, :reader_login,
        :admin_thread, :category, :source, :schedule_id, :repo_full_name, :workspace, :status,
        :latest_run_id, :resolved, :viewed, :title, :created_at, :updated_at, :thread_status,
        CAST(:metadata AS jsonb)
    )
    ON CONFLICT (thread_id) DO UPDATE SET
        listed = EXCLUDED.listed,
        participants = EXCLUDED.participants,
        visibility = EXCLUDED.visibility,
        owner_login = EXCLUDED.owner_login,
        reader_login = EXCLUDED.reader_login,
        admin_thread = EXCLUDED.admin_thread,
        category = EXCLUDED.category,
        source = EXCLUDED.source,
        schedule_id = EXCLUDED.schedule_id,
        repo_full_name = EXCLUDED.repo_full_name,
        workspace = EXCLUDED.workspace,
        status = EXCLUDED.status,
        latest_run_id = EXCLUDED.latest_run_id,
        resolved = EXCLUDED.resolved,
        viewed = EXCLUDED.viewed,
        title = EXCLUDED.title,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at,
        thread_status = EXCLUDED.thread_status,
        metadata = EXCLUDED.metadata,
        seq = nextval('thread_index_seq'),
        synced_at = clock_timestamp()
    """
_UPSERT = text(_UPSERT_SQL).bindparams(bindparam("participants", type_=ARRAY(Text)))
# The reconciler's variant: a row written after its LangGraph read began came
# from a fresher source (write-through or a turn event) and is left alone.
_UPSERT_WRITTEN_BEFORE = text(
    _UPSERT_SQL + " WHERE thread_index.synced_at < :written_before"
).bindparams(bindparam("participants", type_=ARRAY(Text)))


def _transaction(conn: AsyncConnection | None) -> AbstractAsyncContextManager[AsyncConnection]:
    return nullcontext(conn) if conn is not None else postgres.transaction()


def _params(row: ThreadIndexRow) -> dict[str, object]:
    params = asdict(row)
    params["metadata"] = json.dumps(row.metadata)
    return params


async def upsert_thread_index_rows(
    threads: Sequence[ThreadLike],
    *,
    conn: AsyncConnection | None = None,
    written_before: datetime | None = None,
) -> int:
    """Upsert the index rows for ``threads``; returns how many were offered.

    Threads that are not :func:`is_index_candidate` are skipped. With
    ``written_before``, an existing row synced at or after it is kept as is.
    A no-op when PostgreSQL is not configured.
    """
    if conn is None and not postgres.configured():
        return 0
    params = [
        _params(derive_thread_index_row(thread)) for thread in threads if is_index_candidate(thread)
    ]
    if not params:
        return 0
    statement = _UPSERT
    if written_before is not None:
        statement = _UPSERT_WRITTEN_BEFORE
        params = [{**param, "written_before": written_before} for param in params]
    async with _transaction(conn) as tx:
        await tx.execute(statement, params)
    return len(params)


async def upsert_thread_index(thread: ThreadLike, *, conn: AsyncConnection | None = None) -> None:
    await upsert_thread_index_rows([thread], conn=conn)


async def try_upsert_thread_index(thread: ThreadLike) -> None:
    """:func:`upsert_thread_index` for a caller whose LangGraph write already succeeded.

    The reconciler repairs a row this fails to write within one tick, so the
    failure is logged rather than failing the caller.
    """
    try:
        await upsert_thread_index(thread)
    except Exception:
        logger.warning(
            "Could not update the thread index",
            exc_info=True,
            extra={"thread_index": {"thread_id": _thread_id(thread)}},
        )


async def mark_thread_index_status(
    conn: AsyncConnection,
    thread_id: str,
    *,
    status: ThreadIndexStatus,
    run_id: str | None,
) -> None:
    """Record a transcript turn's effect on the thread's row, inside the append transaction.

    A run id the row has not seen makes the thread unread again. Review chats
    are skipped: their status also follows the walkthrough, which only the
    metadata carries, so the write-through and the reconciler own them.
    """
    await conn.execute(
        text(
            """
            UPDATE thread_index SET
                status = :status,
                viewed = CASE
                    WHEN CAST(:run_id AS text) IS NOT NULL
                        AND latest_run_id IS DISTINCT FROM CAST(:run_id AS text)
                    THEN false ELSE viewed END,
                latest_run_id = COALESCE(CAST(:run_id AS text), latest_run_id),
                updated_at = clock_timestamp(),
                seq = nextval('thread_index_seq'),
                synced_at = clock_timestamp()
            WHERE thread_id = :thread_id AND reader_login IS NULL
            """
        ),
        {"thread_id": thread_id, "status": status, "run_id": run_id},
    )


async def delete_thread_index(thread_id: str, *, conn: AsyncConnection | None = None) -> bool:
    """Drop a thread's index row; returns whether one existed."""
    if conn is None and not postgres.configured():
        return False
    async with _transaction(conn) as tx:
        result = await tx.execute(
            text("DELETE FROM thread_index WHERE thread_id = :thread_id RETURNING 1"),
            {"thread_id": thread_id},
        )
        return result.first() is not None


async def load_thread_index_row(
    thread_id: str, *, conn: AsyncConnection | None = None
) -> ThreadIndexRow | None:
    if conn is None and not postgres.configured():
        return None
    async with _transaction(conn) as tx:
        result = await tx.execute(
            text(
                """
                SELECT
                    thread_id, listed, participants, visibility, owner_login, reader_login,
                    admin_thread, category, source, schedule_id, repo_full_name, workspace,
                    status, latest_run_id, resolved, viewed, title, created_at, updated_at,
                    thread_status, metadata
                FROM thread_index
                WHERE thread_id = :thread_id
                """
            ),
            {"thread_id": thread_id},
        )
        record = result.mappings().first()
    if record is None:
        return None
    return ThreadIndexRow(
        thread_id=record["thread_id"],
        listed=record["listed"],
        participants=list(record["participants"]),
        visibility=record["visibility"],
        owner_login=record["owner_login"],
        reader_login=record["reader_login"],
        admin_thread=record["admin_thread"],
        category=record["category"],
        source=record["source"],
        schedule_id=record["schedule_id"],
        repo_full_name=record["repo_full_name"],
        workspace=record["workspace"],
        status=record["status"],
        latest_run_id=record["latest_run_id"],
        resolved=record["resolved"],
        viewed=record["viewed"],
        title=record["title"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        thread_status=record["thread_status"],
        metadata=dict(record["metadata"]),
    )
