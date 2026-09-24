"""Sidebar reads against the ``thread_index`` read model.

Each read is one parameterised query. The WHERE clause is assembled from fixed
fragments chosen by which filters are set; user input only ever reaches the
database as a bound parameter.
"""

import base64
import binascii
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import ARRAY, RowMapping, Text, TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.config import ENV
from agent.dashboard.admin import is_admin
from agent.database import postgres
from agent.threads.summary import _metadata_repo, _ThreadSortBy
from agent.utils.json_types import JsonObject

type ThreadScope = Literal["all", "interactive", "automation"]

_SORT_COLUMNS: Mapping[_ThreadSortBy, str] = {
    "updated_at": "updated_at",
    "created_at": "created_at",
}
_MAX_LIMIT = 100
_SELECT_COLUMNS = (
    "thread_id, thread_status, metadata, created_at, updated_at, status, latest_run_id"
)


def thread_index_reads_enabled() -> bool:
    return ENV.THREAD_INDEX_READS.get_bool() and postgres.configured()


@dataclass(frozen=True, slots=True)
class ThreadCursor:
    """The ``(sort column, thread_id)`` of the last row a page returned."""

    sort_value: datetime
    thread_id: str


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _epoch_us(value: datetime) -> int:
    delta = value - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def encode_thread_cursor(cursor: ThreadCursor) -> str:
    raw = f"{_epoch_us(cursor.sort_value)}:{cursor.thread_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_thread_cursor(value: str) -> ThreadCursor | None:
    """The cursor ``value`` encodes, or ``None`` when it is malformed."""
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
    except binascii.Error, UnicodeDecodeError, ValueError:
        return None
    epoch, separator, thread_id = raw.partition(":")
    if not separator or not thread_id or not (epoch.isascii() and epoch.isdigit()):
        return None
    try:
        sort_value = _EPOCH + timedelta(microseconds=int(epoch))
    except OverflowError:
        return None
    return ThreadCursor(sort_value=sort_value, thread_id=thread_id)


@dataclass(frozen=True, slots=True)
class ThreadListFilters:
    """The sidebar list parameters of ``list_dashboard_threads_page``.

    ``include_all`` drops the participant predicate, so the caller must have
    verified the viewer is an admin. ``surfaced_only`` needs no predicate: a
    row is only ``listed`` when its source is surfaced.
    """

    login: str
    email: str | None = None
    include_all: bool = False
    resolved: bool | None = None
    viewed: bool | None = None
    source: str | None = None
    status: str | None = None
    query: str | None = None
    scope: ThreadScope = "all"
    automation_id: str | None = None
    repo: str | None = None
    ownerless: bool = False
    filter_participant_login: str | None = None
    include_private: bool = True
    surfaced_only: bool = False
    admin_threads: bool | None = None
    sort_by: _ThreadSortBy = "updated_at"
    limit: int = 25
    offset: int = 0
    cursor: ThreadCursor | None = None


@dataclass(frozen=True, slots=True)
class IndexedThread:
    """A row as the LangGraph thread shape the summary reads, plus the row's own state.

    ``status`` and ``latest_run_id`` can be newer than ``thread["metadata"]``:
    transcript turn events update the row without rewriting the metadata.
    """

    thread: JsonObject
    status: str
    latest_run_id: str | None
    cursor: ThreadCursor


@dataclass(frozen=True, slots=True)
class ThreadIndexPage:
    threads: list[IndexedThread]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RepoRow:
    repo_full_name: str
    name: str
    updated_at_ms: int


def _read(conn: AsyncConnection | None) -> AbstractAsyncContextManager[AsyncConnection]:
    return nullcontext(conn) if conn is not None else postgres.snapshot_transaction()


def _lowered(value: str | None) -> str | None:
    return value.strip().lower() if value and value.strip() else None


def like_pattern(query: str) -> str:
    """A substring ILIKE pattern that matches ``query`` literally."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_QUERY_PREDICATE = """(
    COALESCE(title, 'Untitled agent') ILIKE :pattern ESCAPE '\\'
    OR repo_full_name ILIKE :pattern ESCAPE '\\'
    OR metadata->>'branch_name' ILIKE :pattern ESCAPE '\\'
    OR metadata->>'base_branch' ILIKE :pattern ESCAPE '\\'
    OR metadata->>'pr_url' ILIKE :pattern ESCAPE '\\'
    OR metadata->>'pr_number' ILIKE :pattern ESCAPE '\\'
    OR EXISTS (
        SELECT 1
        FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(metadata->'pull_requests') = 'array'
            THEN metadata->'pull_requests' ELSE '[]'::jsonb END
        ) AS pr(record)
        CROSS JOIN LATERAL jsonb_each_text(
            CASE WHEN jsonb_typeof(pr.record) = 'object' THEN pr.record ELSE '{}'::jsonb END
        ) AS field
        WHERE field.value ILIKE :pattern ESCAPE '\\'
    )
)"""
_READABLE_PREDICATE = """(
    (reader_login IS NULL AND (visibility = 'public' OR owner_login = :login OR :is_admin))
    OR reader_login = :login
)"""


def _where(filters: ThreadListFilters) -> tuple[list[str], dict[str, object]]:
    """The filter predicate shared by the page and repos queries, without the cursor."""
    login = _lowered(filters.login) or ""
    clauses = ["listed", _READABLE_PREDICATE]
    params: dict[str, object] = {
        "login": login,
        "is_admin": is_admin(filters.email, login=filters.login),
    }
    participant = _lowered(filters.filter_participant_login)
    # The automation view lists every automation thread, not only the viewer's.
    automation_view = filters.scope == "automation" and participant is None
    if not filters.include_all and not automation_view:
        if participant is not None:
            clauses.append("participants @> ARRAY[CAST(:participant AS text)]")
            params["participant"] = participant
        else:
            clauses.append("participants && :principals")
            params["principals"] = [
                principal for principal in (login, _lowered(filters.email)) if principal is not None
            ]
    if not filters.include_private:
        clauses.append("visibility = 'public'")
    if filters.admin_threads is not None:
        clauses.append("admin_thread = :admin_threads")
        params["admin_threads"] = filters.admin_threads
    if filters.scope == "automation":
        clauses.append("category = 'automation'")
    elif filters.scope == "interactive":
        clauses.append("category <> 'automation'")
    if filters.resolved is not None:
        clauses.append("resolved = :resolved")
        params["resolved"] = filters.resolved
    if filters.viewed is not None:
        clauses.append("viewed = :viewed")
        params["viewed"] = filters.viewed
    if filters.status:
        clauses.append("status = :status")
        params["status"] = filters.status
    if filters.source:
        clauses.append("source = :source")
        params["source"] = filters.source
    if filters.automation_id:
        clauses.append("schedule_id = :automation_id")
        params["automation_id"] = filters.automation_id
    if repo := _lowered(filters.repo):
        clauses.append("repo_full_name = :repo")
        params["repo"] = repo
    if filters.ownerless:
        clauses.append("repo_full_name IS NULL")
    if filters.query and filters.query.strip():
        clauses.append(_QUERY_PREDICATE)
        params["pattern"] = like_pattern(filters.query.strip())
    return clauses, params


def _statement(sql: str, params: Mapping[str, object]) -> TextClause:
    statement = text(sql)
    if "principals" in params:
        statement = statement.bindparams(bindparam("principals", type_=ARRAY(Text)))
    return statement


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"expected a timestamp, got {type(value).__name__}")
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _epoch_ms(value: object) -> int:
    return _epoch_us(_utc(value)) // 1000


def _indexed_thread(record: RowMapping, sort_by: _ThreadSortBy) -> IndexedThread:
    created_at = _utc(record["created_at"])
    updated_at = _utc(record["updated_at"])
    raw_metadata = record["metadata"]
    metadata: JsonObject = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    # The row's timestamps are what the page is sorted by; a turn event bumps
    # them without rewriting the metadata.
    metadata["created_at_ms"] = _epoch_ms(created_at)
    metadata["updated_at_ms"] = _epoch_ms(updated_at)
    thread_id = str(record["thread_id"])
    latest_run_id = record["latest_run_id"]
    return IndexedThread(
        thread={
            "thread_id": thread_id,
            "status": record["thread_status"],
            "metadata": metadata,
            "created_at": created_at.isoformat(),
            "updated_at": updated_at.isoformat(),
        },
        status=str(record["status"]),
        latest_run_id=latest_run_id if isinstance(latest_run_id, str) else None,
        cursor=ThreadCursor(
            sort_value=created_at if sort_by == "created_at" else updated_at,
            thread_id=thread_id,
        ),
    )


async def list_thread_index_page(
    filters: ThreadListFilters, *, conn: AsyncConnection | None = None
) -> ThreadIndexPage:
    """One page of the sidebar, newest first, keyset-paged by ``filters.cursor``.

    Without a cursor, ``filters.offset`` is honoured so offset clients keep working.
    """
    clauses, params = _where(filters)
    sort_column = _SORT_COLUMNS[filters.sort_by]
    limit = min(max(filters.limit, 1), _MAX_LIMIT)
    params["limit"] = limit + 1
    offset_sql = ""
    if filters.cursor is not None:
        clauses.append(f"({sort_column}, thread_id) < (:cursor_value, :cursor_id)")
        params["cursor_value"] = filters.cursor.sort_value
        params["cursor_id"] = filters.cursor.thread_id
    elif filters.offset > 0:
        offset_sql = " OFFSET :offset"
        params["offset"] = filters.offset
    sql = (
        f"SELECT {_SELECT_COLUMNS} FROM thread_index WHERE {' AND '.join(clauses)} "
        f"ORDER BY {sort_column} DESC, thread_id DESC LIMIT :limit{offset_sql}"
    )
    async with _read(conn) as tx:
        result = await tx.execute(_statement(sql, params), params)
        records = result.mappings().all()
    threads = [_indexed_thread(record, filters.sort_by) for record in records[:limit]]
    has_more = len(records) > limit
    return ThreadIndexPage(
        threads=threads,
        has_more=has_more,
        next_cursor=encode_thread_cursor(threads[-1].cursor) if has_more and threads else None,
    )


async def list_thread_index_repos(
    filters: ThreadListFilters, *, conn: AsyncConnection | None = None
) -> list[RepoRow]:
    """The repositories the filtered threads ran in, most recently updated first."""
    clauses, params = _where(filters)
    clauses.append("repo_full_name IS NOT NULL")
    sql = f"""
        SELECT
            repo_full_name,
            max(updated_at) AS updated_at,
            (array_agg(
                jsonb_build_object(
                    'repo_owner', metadata->'repo_owner',
                    'repo_name', metadata->'repo_name',
                    'repo', metadata->'repo'
                )
                ORDER BY updated_at DESC
            ))[1] AS repo
        FROM thread_index
        WHERE {" AND ".join(clauses)}
        GROUP BY repo_full_name
        ORDER BY max(updated_at) DESC, repo_full_name
    """
    async with _read(conn) as tx:
        result = await tx.execute(_statement(sql, params), params)
        records = result.mappings().all()
    repos: list[RepoRow] = []
    for record in records:
        repo = record["repo"]
        _, name, full_name = _metadata_repo(repo if isinstance(repo, Mapping) else {})
        key = str(record["repo_full_name"])
        repos.append(
            RepoRow(
                repo_full_name=full_name or key,
                name=name or key.partition("/")[2],
                updated_at_ms=_epoch_ms(record["updated_at"]),
            )
        )
    return repos


async def load_thread_index_threads(
    thread_ids: Sequence[str], *, conn: AsyncConnection | None = None
) -> list[IndexedThread]:
    """The index rows for ``thread_ids`` in no particular order; unknown ids are absent.

    Unlike the list, this does not require ``listed`` or apply readability: the
    caller decides, as pins of unlisted threads stay visible.
    """
    if not thread_ids:
        return []
    statement = text(
        f"SELECT {_SELECT_COLUMNS} FROM thread_index WHERE thread_id = ANY(:ids)"
    ).bindparams(bindparam("ids", type_=ARRAY(Text)))
    async with _read(conn) as tx:
        result = await tx.execute(statement, {"ids": list(thread_ids)})
        records = result.mappings().all()
    return [_indexed_thread(record, "updated_at") for record in records]
