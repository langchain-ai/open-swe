"""Durable registry for thread identity, lifecycle, transcripts, and events."""

import asyncio
import base64
import builtins
import json
import os
import sqlite3
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

try:
    import asyncpg
except ImportError:  # pragma: no cover - exercised only before dependencies are installed
    asyncpg = None  # type: ignore[assignment]

ThreadEnvironment = Literal["cloud", "local"]
ThreadStatus = Literal["idle", "queued", "running", "interrupted", "finished", "error"]
RunStatus = Literal["queued", "running", "interrupted", "finished", "error"]

THREAD_ENVIRONMENTS = frozenset({"cloud", "local"})
THREAD_STATUSES = frozenset({"idle", "queued", "running", "interrupted", "finished", "error"})
RUN_STATUSES = frozenset({"queued", "running", "interrupted", "finished", "error"})
TERMINAL_STATUSES = frozenset({"interrupted", "finished", "error"})
MAX_MESSAGE_PAYLOAD_BYTES = 256 * 1024
MAX_MESSAGE_BATCH = 100


def utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _json_object(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _json_payload(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _cursor(updated_at: datetime, thread_id: str) -> str:
    raw = _json([_iso(updated_at), thread_id]).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(decoded, list) or len(decoded) != 2 or not isinstance(decoded[1], str):
            raise ValueError
        updated_at = _as_utc(decoded[0]) if isinstance(decoded[0], str) else None
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid thread cursor") from exc
    if updated_at is None or not decoded[1]:
        raise ValueError("invalid thread cursor")
    return updated_at, decoded[1]


@dataclass(slots=True)
class ThreadCreate:
    id: str
    owner_login: str
    owner_email: str | None = None
    title: str = "Untitled agent"
    repo_full_name: str | None = None
    branch: str | None = None
    environment: ThreadEnvironment = "cloud"
    device_id: str | None = None
    device_name: str | None = None
    source: str = "dashboard"
    category: str = "interactive"
    trigger_kind: str = "user"
    automation_id: str | None = None
    plan_status: str | None = None
    model: str | None = None
    effort: str | None = None
    sandbox_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class ThreadRow:
    id: str
    owner_login: str
    owner_email: str | None
    title: str
    repo_full_name: str | None
    branch: str | None
    environment: str
    device_id: str | None
    device_name: str | None
    status: str
    status_run_id: str | None
    status_at: datetime
    source: str
    category: str
    trigger_kind: str
    automation_id: str | None
    plan_status: str | None
    model: str | None
    effort: str | None
    resolved: bool
    resolved_at: datetime | None
    viewed_run_id: str | None
    sandbox_id: str | None
    git_checkpoint: dict[str, Any] | None
    pr_state: dict[str, Any] | None
    diff_stats: dict[str, Any] | None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    last_finished_run_id: str | None = None

    @property
    def viewed(self) -> bool:
        return self.last_finished_run_id is None or self.viewed_run_id == self.last_finished_run_id

    def api_dict(self, *, is_owner: bool = True) -> dict[str, Any]:
        repo = self.repo_full_name or ""
        result: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "repo": repo.split("/", 1)[1] if "/" in repo else repo,
            "repoFullName": repo,
            "branch": self.branch or "main",
            "environment": self.environment,
            "deviceId": self.device_id,
            "deviceName": self.device_name,
            "model": self.model or "Default",
            "effort": self.effort,
            "planStatus": self.plan_status,
            "source": self.source,
            "origin": self.metadata.get("origin", self.source),
            "threadCategory": self.category,
            "triggerKind": self.trigger_kind,
            "automationId": self.automation_id,
            "automationName": self.metadata.get("automation_name"),
            "automationActionPosted": bool(self.metadata.get("automation_action_posted_at")),
            "status": self.status,
            "statusRunId": self.status_run_id,
            "statusAt": _iso(self.status_at),
            "viewed": self.viewed,
            "viewedRunId": self.viewed_run_id,
            "resolved": self.resolved,
            "resolvedAt": int(self.resolved_at.timestamp() * 1000) if self.resolved_at else None,
            "createdAt": int(self.created_at.timestamp() * 1000),
            "updatedAt": int(self.updated_at.timestamp() * 1000),
            "ownerLogin": self.owner_login,
            "isOwner": is_owner,
            "sandboxId": self.sandbox_id,
            "gitCheckpoint": self.git_checkpoint,
            "prState": self.pr_state,
            "diffStats": self.diff_stats,
            "traceUrl": self.metadata.get("trace_url"),
            "sourceUrl": self.metadata.get("source_url"),
            "adminThread": self.metadata.get("admin_thread") is True,
            "planMode": self.metadata.get("plan_mode") is True,
            "messages": [],
        }
        pull_requests = self.metadata.get("pull_requests")
        if isinstance(pull_requests, list):
            result["pullRequests"] = pull_requests
        if isinstance(self.pr_state, dict):
            result["pr"] = self.pr_state
        return result


@dataclass(slots=True)
class ThreadPage:
    items: list[ThreadRow]
    cursor: str | None
    has_more: bool


@dataclass(slots=True)
class ThreadEvent:
    id: int
    thread_id: str
    owner_login: str
    kind: str
    payload: dict[str, Any]
    created_at: datetime

    def api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "kind": self.kind,
            "payload": self.payload,
            "created_at": _iso(self.created_at),
        }


class ThreadRegistry(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def create(self, value: ThreadCreate) -> ThreadRow: ...
    async def get(self, thread_id: str) -> ThreadRow | None: ...
    async def list(
        self,
        owner: str | None,
        *,
        resolved: bool | None = None,
        environment: str | None = None,
        source: str | None = None,
        status: str | None = None,
        q: str | None = None,
        scope: str = "all",
        automation_id: str | None = None,
        viewed: bool | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ThreadPage: ...
    async def transition(
        self,
        thread_id: str,
        run_id: str,
        status: RunStatus,
        *,
        environment: ThreadEnvironment | None = None,
        device_id: str | None = None,
        error: str | None = None,
        guard_run_id: str | None = None,
    ) -> ThreadRow: ...
    async def update_meta(self, thread_id: str, **fields: Any) -> ThreadRow: ...
    async def delete(self, thread_id: str) -> bool: ...
    async def append_messages(
        self, thread_id: str, run_id: str | None, messages: Sequence[Mapping[str, Any]]
    ) -> int: ...
    async def get_messages(
        self, thread_id: str, *, after_seq: int = 0
    ) -> builtins.list[dict[str, Any]]: ...
    async def events_since(
        self, cursor: int, owner: str | None, *, limit: int = 500
    ) -> builtins.list[ThreadEvent]: ...
    async def wait_for_events(self, timeout: float = 15.0) -> None: ...
    async def prune_events(self, *, older_than: datetime) -> int: ...
    async def record_heartbeat(
        self, device_id: str, owner_login: str, device_name: str
    ) -> None: ...
    async def device(self, device_id: str, owner_login: str) -> dict[str, Any] | None: ...


_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY,
  owner_login TEXT NOT NULL,
  owner_email TEXT,
  title TEXT NOT NULL DEFAULT 'Untitled agent',
  repo_full_name TEXT,
  branch TEXT,
  environment TEXT NOT NULL,
  device_id TEXT,
  device_name TEXT,
  status TEXT NOT NULL DEFAULT 'idle',
  status_run_id TEXT,
  status_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'dashboard',
  category TEXT NOT NULL DEFAULT 'interactive',
  trigger_kind TEXT NOT NULL DEFAULT 'user',
  automation_id TEXT,
  plan_status TEXT,
  model TEXT,
  effort TEXT,
  resolved INTEGER NOT NULL DEFAULT 0,
  resolved_at TEXT,
  viewed_run_id TEXT,
  sandbox_id TEXT,
  git_checkpoint TEXT,
  pr_state TEXT,
  diff_stats TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS threads_owner_updated ON threads(owner_login, resolved, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS threads_automation ON threads(automation_id, updated_at DESC) WHERE automation_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS thread_runs (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  environment TEXT NOT NULL,
  device_id TEXT,
  status TEXT NOT NULL,
  error TEXT,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS thread_runs_thread ON thread_runs(thread_id, created_at DESC);
CREATE TABLE IF NOT EXISTS thread_messages (
  thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  run_id TEXT,
  message_id TEXT,
  author TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(thread_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS thread_messages_identity ON thread_messages(thread_id, message_id) WHERE message_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS thread_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id TEXT NOT NULL,
  owner_login TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS thread_events_owner_id ON thread_events(owner_login, id);
CREATE TABLE IF NOT EXISTS thread_devices (
  id TEXT NOT NULL,
  owner_login TEXT NOT NULL,
  name TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY(id, owner_login)
);
"""


_THREAD_SELECT = """
SELECT t.*,
       (SELECT r.id FROM thread_runs r
        WHERE r.thread_id=t.id AND r.status='finished'
        ORDER BY r.created_at DESC LIMIT 1) AS last_finished_run_id
FROM threads t
"""


def _thread_from_row(row: Mapping[str, Any]) -> ThreadRow:
    return ThreadRow(
        id=str(row["id"]),
        owner_login=str(row["owner_login"]),
        owner_email=row["owner_email"],
        title=str(row["title"]),
        repo_full_name=row["repo_full_name"],
        branch=row["branch"],
        environment=str(row["environment"]),
        device_id=row["device_id"],
        device_name=row["device_name"],
        status=str(row["status"]),
        status_run_id=row["status_run_id"],
        status_at=cast(datetime, _as_utc(row["status_at"])),
        source=str(row["source"]),
        category=str(row["category"]),
        trigger_kind=str(row["trigger_kind"]),
        automation_id=row["automation_id"],
        plan_status=row["plan_status"],
        model=row["model"],
        effort=row["effort"],
        resolved=bool(row["resolved"]),
        resolved_at=_as_utc(row["resolved_at"]),
        viewed_run_id=row["viewed_run_id"],
        sandbox_id=row["sandbox_id"],
        git_checkpoint=_json_object(row["git_checkpoint"]),
        pr_state=_json_object(row["pr_state"]),
        diff_stats=_json_object(row["diff_stats"]),
        metadata=_json_payload(row["metadata"]),
        created_at=cast(datetime, _as_utc(row["created_at"])),
        updated_at=cast(datetime, _as_utc(row["updated_at"])),
        last_finished_run_id=row["last_finished_run_id"],
    )


def _event_from_row(row: Mapping[str, Any]) -> ThreadEvent:
    return ThreadEvent(
        id=int(row["id"]),
        thread_id=str(row["thread_id"]),
        owner_login=str(row["owner_login"]),
        kind=str(row["kind"]),
        payload=_json_payload(row["payload"]),
        created_at=cast(datetime, _as_utc(row["created_at"])),
    )


def _validate_create(value: ThreadCreate) -> None:
    if not value.id or len(value.id) > 128:
        raise ValueError("invalid thread id")
    if not value.owner_login or len(value.owner_login) > 255:
        raise ValueError("invalid thread owner")
    if value.environment not in THREAD_ENVIRONMENTS:
        raise ValueError("invalid thread environment")
    if value.environment == "local" and not value.device_id:
        raise ValueError("local threads require a device id")


def _validate_transition(status: str) -> None:
    if status not in RUN_STATUSES:
        raise ValueError("invalid run status")


def _transition_allowed(current: str, new: str, current_run: str | None, run_id: str) -> bool:
    if new == "queued":
        return current in THREAD_STATUSES
    if current_run != run_id:
        return False
    if current == new:
        return True
    return (current, new) in {
        ("queued", "running"),
        ("queued", "finished"),
        ("queued", "interrupted"),
        ("queued", "error"),
        ("running", "interrupted"),
        ("running", "finished"),
        ("running", "error"),
    }


def _transition_is_stale(
    current: str,
    current_run: str | None,
    run_id: str,
    status: str,
    guard_run_id: str | None,
) -> bool:
    if guard_run_id is not None and current_run != guard_run_id:
        return True
    if status != "queued":
        return current_run != run_id
    return guard_run_id is None and current in {"queued", "running"} and current_run != run_id


def _bounded_payload(message: Mapping[str, Any]) -> tuple[str, str, str | None]:
    payload = (
        dict(message.get("payload")) if isinstance(message.get("payload"), dict) else dict(message)
    )
    author = message.get("author") or payload.get("author") or "system"
    if author not in {"user", "agent", "system", "tool"}:
        author = "system"
    message_id = payload.get("id")
    message_id = message_id if isinstance(message_id, str) and message_id else None
    encoded = _json(payload)
    if len(encoded.encode()) > MAX_MESSAGE_PAYLOAD_BYTES:
        payload = {
            "id": message_id or "truncated",
            "author": author,
            "timestamp": payload.get("timestamp") or _iso(utcnow()),
            "chunks": [{"kind": "error", "text": "Message content was truncated."}],
            "isTruncated": True,
        }
        encoded = _json(payload)
    return str(author), encoded, message_id


class SqliteRegistry:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition()

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            if self.path != ":memory:":
                Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if self.path != ":memory:":
                connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA_SQLITE)
            connection.commit()
            self._connection = connection

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("thread registry is not initialized")
        return self._connection

    async def _notify(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def create(self, value: ThreadCreate) -> ThreadRow:
        _validate_create(value)
        await self.initialize()
        now = utcnow()
        created = value.created_at or now
        updated = value.updated_at or created
        async with self._lock:
            conn = self._conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT INTO threads(
                         id,owner_login,owner_email,title,repo_full_name,branch,environment,
                         device_id,device_name,status,status_at,source,category,trigger_kind,
                         automation_id,plan_status,model,effort,sandbox_id,metadata,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,'idle',?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO NOTHING""",
                    (
                        value.id,
                        value.owner_login,
                        value.owner_email,
                        value.title[:500],
                        value.repo_full_name,
                        value.branch,
                        value.environment,
                        value.device_id,
                        value.device_name,
                        _iso(created),
                        value.source,
                        value.category,
                        value.trigger_kind,
                        value.automation_id,
                        value.plan_status,
                        value.model,
                        value.effort,
                        value.sandbox_id,
                        _json(value.metadata),
                        _iso(created),
                        _iso(updated),
                    ),
                )
                inserted = conn.execute("SELECT changes()").fetchone()[0] > 0
                row = conn.execute(f"{_THREAD_SELECT} WHERE t.id=?", (value.id,)).fetchone()
                if row is None:
                    raise RuntimeError("thread create failed")
                if inserted:
                    self._insert_event(
                        conn,
                        value.id,
                        value.owner_login,
                        "thread.created",
                        _thread_from_row(row).api_dict(),
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        if inserted:
            await self._notify()
        return _thread_from_row(row)

    async def get(self, thread_id: str) -> ThreadRow | None:
        await self.initialize()
        async with self._lock:
            row = self._conn().execute(f"{_THREAD_SELECT} WHERE t.id=?", (thread_id,)).fetchone()
        return _thread_from_row(row) if row else None

    async def list(
        self,
        owner: str | None,
        *,
        resolved: bool | None = None,
        environment: str | None = None,
        source: str | None = None,
        status: str | None = None,
        q: str | None = None,
        scope: str = "all",
        automation_id: str | None = None,
        viewed: bool | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ThreadPage:
        await self.initialize()
        if environment is not None and environment not in THREAD_ENVIRONMENTS:
            raise ValueError("invalid environment filter")
        if status is not None and status not in THREAD_STATUSES:
            raise ValueError("invalid status filter")
        if scope not in {"all", "interactive", "automation"}:
            raise ValueError("invalid thread scope")
        after = decode_cursor(cursor)
        clauses: list[str] = []
        values: list[Any] = []
        if owner is not None:
            clauses.append("t.owner_login=?")
            values.append(owner)
        if resolved is not None:
            clauses.append("t.resolved=?")
            values.append(int(resolved))
        if environment is not None:
            clauses.append("t.environment=?")
            values.append(environment)
        if source is not None:
            clauses.append("t.source=?")
            values.append(source)
        if status is not None:
            clauses.append("t.status=?")
            values.append(status)
        if q:
            clauses.append("LOWER(t.title) LIKE ? ESCAPE '\\'")
            escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.append(f"%{escaped}%")
        if scope == "interactive":
            clauses.append("t.category<>'automation'")
        elif scope == "automation":
            clauses.append("t.category='automation'")
        if automation_id is not None:
            clauses.append("t.automation_id=?")
            values.append(automation_id)
        if viewed is not None:
            operator = "=" if viewed else "<>"
            clauses.append(
                f"COALESCE(t.viewed_run_id,'') {operator} COALESCE((SELECT vr.id FROM thread_runs vr WHERE vr.thread_id=t.id AND vr.status='finished' ORDER BY vr.created_at DESC LIMIT 1),'')"
            )
        if after is not None:
            clauses.append("(t.updated_at < ? OR (t.updated_at = ? AND t.id < ?))")
            stamp = _iso(after[0])
            values.extend((stamp, stamp, after[1]))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        bounded_limit = max(1, min(limit, 200))
        values.append(bounded_limit + 1)
        sql = f"{_THREAD_SELECT}{where} ORDER BY t.updated_at DESC,t.id DESC LIMIT ?"
        async with self._lock:
            rows = self._conn().execute(sql, values).fetchall()
        has_more = len(rows) > bounded_limit
        items = [_thread_from_row(row) for row in rows[:bounded_limit]]
        next_cursor = _cursor(items[-1].updated_at, items[-1].id) if has_more and items else None
        return ThreadPage(items=items, cursor=next_cursor, has_more=has_more)

    async def transition(
        self,
        thread_id: str,
        run_id: str,
        status: RunStatus,
        *,
        environment: ThreadEnvironment | None = None,
        device_id: str | None = None,
        error: str | None = None,
        guard_run_id: str | None = None,
    ) -> ThreadRow:
        _validate_transition(status)
        await self.initialize()
        now = utcnow()
        async with self._lock:
            conn = self._conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
                if current is None:
                    raise KeyError(thread_id)
                if _transition_is_stale(
                    str(current["status"]),
                    current["status_run_id"],
                    run_id,
                    status,
                    guard_run_id,
                ):
                    row = conn.execute(f"{_THREAD_SELECT} WHERE t.id=?", (thread_id,)).fetchone()
                    conn.commit()
                    return _thread_from_row(row)
                if not _transition_allowed(
                    str(current["status"]), status, current["status_run_id"], run_id
                ):
                    raise ValueError(f"invalid transition {current['status']} -> {status}")
                run_environment = environment or current["environment"]
                conn.execute(
                    """INSERT INTO thread_runs(id,thread_id,environment,device_id,status,error,started_at,finished_at,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         status=excluded.status,error=excluded.error,
                         started_at=COALESCE(thread_runs.started_at,excluded.started_at),
                         finished_at=excluded.finished_at""",
                    (
                        run_id,
                        thread_id,
                        run_environment,
                        device_id,
                        status,
                        error,
                        _iso(now) if status == "running" else None,
                        _iso(now) if status in TERMINAL_STATUSES else None,
                        _iso(now),
                    ),
                )
                conn.execute(
                    """UPDATE threads SET status=?,status_run_id=?,status_at=?,updated_at=?,
                         environment=COALESCE(?,environment),device_id=CASE WHEN ?='cloud' THEN NULL ELSE COALESCE(?,device_id) END
                       WHERE id=?""",
                    (
                        status,
                        run_id,
                        _iso(now),
                        _iso(now),
                        environment,
                        environment,
                        device_id,
                        thread_id,
                    ),
                )
                row = conn.execute(f"{_THREAD_SELECT} WHERE t.id=?", (thread_id,)).fetchone()
                thread = _thread_from_row(row)
                self._insert_event(
                    conn, thread_id, thread.owner_login, "thread.status", thread.api_dict()
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        await self._notify()
        return thread

    async def update_meta(self, thread_id: str, **fields: Any) -> ThreadRow:
        allowed = {
            "title",
            "repo_full_name",
            "branch",
            "environment",
            "device_id",
            "device_name",
            "source",
            "category",
            "trigger_kind",
            "automation_id",
            "plan_status",
            "model",
            "effort",
            "resolved",
            "resolved_at",
            "viewed_run_id",
            "sandbox_id",
            "git_checkpoint",
            "pr_state",
            "diff_stats",
            "metadata",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported thread fields: {', '.join(sorted(unknown))}")
        if fields.get("environment") not in (None, *THREAD_ENVIRONMENTS):
            raise ValueError("invalid thread environment")
        await self.initialize()
        now = utcnow()
        encoded: dict[str, Any] = {}
        for key, value in fields.items():
            if key in {"git_checkpoint", "pr_state", "diff_stats", "metadata"}:
                value = _json(value) if value is not None else None
            elif key in {"resolved_at"} and isinstance(value, datetime):
                value = _iso(value)
            elif key == "resolved":
                value = int(bool(value))
            encoded[key] = value
        encoded["updated_at"] = _iso(now)
        assignments = ",".join(f"{key}=?" for key in encoded)
        values = [*encoded.values(), thread_id]
        async with self._lock:
            conn = self._conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                changed = conn.execute(
                    f"UPDATE threads SET {assignments} WHERE id=?", values
                ).rowcount
                if not changed:
                    raise KeyError(thread_id)
                row = conn.execute(f"{_THREAD_SELECT} WHERE t.id=?", (thread_id,)).fetchone()
                thread = _thread_from_row(row)
                kind = "thread.handoff" if "environment" in fields else "thread.meta"
                self._insert_event(conn, thread_id, thread.owner_login, kind, thread.api_dict())
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        await self._notify()
        return thread

    async def delete(self, thread_id: str) -> bool:
        await self.initialize()
        async with self._lock:
            conn = self._conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                owner = conn.execute(
                    "SELECT owner_login FROM threads WHERE id=?", (thread_id,)
                ).fetchone()
                if owner is None:
                    conn.rollback()
                    return False
                self._insert_event(
                    conn, thread_id, owner["owner_login"], "thread.deleted", {"id": thread_id}
                )
                conn.execute("DELETE FROM threads WHERE id=?", (thread_id,))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        await self._notify()
        return True

    async def append_messages(
        self, thread_id: str, run_id: str | None, messages: Sequence[Mapping[str, Any]]
    ) -> int:
        await self.initialize()
        if len(messages) > MAX_MESSAGE_BATCH:
            raise ValueError("message batch too large")
        async with self._lock:
            conn = self._conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                owner = conn.execute(
                    "SELECT owner_login FROM threads WHERE id=?", (thread_id,)
                ).fetchone()
                if owner is None:
                    raise KeyError(thread_id)
                seq = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(seq),0) FROM thread_messages WHERE thread_id=?",
                        (thread_id,),
                    ).fetchone()[0]
                )
                appended = 0
                for message in messages:
                    author, payload, message_id = _bounded_payload(message)
                    if (
                        message_id
                        and conn.execute(
                            "SELECT 1 FROM thread_messages WHERE thread_id=? AND message_id=?",
                            (thread_id, message_id),
                        ).fetchone()
                    ):
                        continue
                    seq += 1
                    conn.execute(
                        "INSERT INTO thread_messages(thread_id,seq,run_id,message_id,author,payload,created_at) VALUES(?,?,?,?,?,?,?)",
                        (thread_id, seq, run_id, message_id, author, payload, _iso(utcnow())),
                    )
                    appended += 1
                if appended:
                    conn.execute(
                        "UPDATE threads SET updated_at=? WHERE id=?", (_iso(utcnow()), thread_id)
                    )
                    self._insert_event(
                        conn,
                        thread_id,
                        owner["owner_login"],
                        "thread.message",
                        {"thread_id": thread_id, "seq": seq},
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        if appended:
            await self._notify()
        return seq

    async def get_messages(
        self, thread_id: str, *, after_seq: int = 0
    ) -> builtins.list[dict[str, Any]]:
        await self.initialize()
        async with self._lock:
            rows = (
                self._conn()
                .execute(
                    "SELECT seq,run_id,author,payload,created_at FROM thread_messages WHERE thread_id=? AND seq>? ORDER BY seq LIMIT 1000",
                    (thread_id, max(0, after_seq)),
                )
                .fetchall()
            )
        return [
            {
                "seq": int(row["seq"]),
                "run_id": row["run_id"],
                "author": row["author"],
                "payload": _json_payload(row["payload"]),
                "created_at": _iso(cast(datetime, _as_utc(row["created_at"]))),
            }
            for row in rows
        ]

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        thread_id: str,
        owner_login: str,
        kind: str,
        payload: Mapping[str, Any],
    ) -> int:
        cursor = conn.execute(
            "INSERT INTO thread_events(thread_id,owner_login,kind,payload,created_at) VALUES(?,?,?,?,?)",
            (thread_id, owner_login, kind, _json(payload), _iso(utcnow())),
        )
        return int(cursor.lastrowid or 0)

    async def events_since(
        self, cursor: int, owner: str | None, *, limit: int = 500
    ) -> builtins.list[ThreadEvent]:
        await self.initialize()
        clauses = ["id>?"]
        values: list[Any] = [max(0, cursor)]
        if owner is not None:
            clauses.append("owner_login=?")
            values.append(owner)
        values.append(max(1, min(limit, 1000)))
        sql = f"SELECT * FROM thread_events WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?"
        async with self._lock:
            rows = self._conn().execute(sql, values).fetchall()
        return [_event_from_row(row) for row in rows]

    async def wait_for_events(self, timeout: float = 15.0) -> None:
        try:
            async with self._condition:
                await asyncio.wait_for(self._condition.wait(), timeout)
        except TimeoutError:
            pass

    async def prune_events(self, *, older_than: datetime) -> int:
        await self.initialize()
        async with self._lock:
            conn = self._conn()
            cursor = conn.execute(
                "DELETE FROM thread_events WHERE created_at<?", (_iso(older_than),)
            )
            conn.commit()
            return cursor.rowcount

    async def record_heartbeat(self, device_id: str, owner_login: str, device_name: str) -> None:
        await self.initialize()
        async with self._lock:
            self._conn().execute(
                """INSERT INTO thread_devices(id,owner_login,name,last_seen_at) VALUES(?,?,?,?)
                   ON CONFLICT(id,owner_login) DO UPDATE SET name=excluded.name,last_seen_at=excluded.last_seen_at""",
                (device_id, owner_login, device_name[:255], _iso(utcnow())),
            )
            self._conn().commit()

    async def device(self, device_id: str, owner_login: str) -> dict[str, Any] | None:
        await self.initialize()
        async with self._lock:
            row = (
                self._conn()
                .execute(
                    "SELECT * FROM thread_devices WHERE id=? AND owner_login=?",
                    (device_id, owner_login),
                )
                .fetchone()
            )
        return dict(row) if row else None


_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY, owner_login TEXT NOT NULL, owner_email TEXT,
  title TEXT NOT NULL DEFAULT 'Untitled agent', repo_full_name TEXT, branch TEXT,
  environment TEXT NOT NULL, device_id TEXT, device_name TEXT,
  status TEXT NOT NULL DEFAULT 'idle', status_run_id TEXT,
  status_at TIMESTAMPTZ NOT NULL DEFAULT now(), source TEXT NOT NULL DEFAULT 'dashboard',
  category TEXT NOT NULL DEFAULT 'interactive', trigger_kind TEXT NOT NULL DEFAULT 'user',
  automation_id TEXT, plan_status TEXT, model TEXT, effort TEXT,
  resolved BOOLEAN NOT NULL DEFAULT FALSE, resolved_at TIMESTAMPTZ, viewed_run_id TEXT,
  sandbox_id TEXT, git_checkpoint JSONB, pr_state JSONB, diff_stats JSONB,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS threads_owner_updated ON threads(owner_login,resolved,updated_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS threads_automation ON threads(automation_id,updated_at DESC) WHERE automation_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS thread_runs (
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
 environment TEXT NOT NULL, device_id TEXT, status TEXT NOT NULL, error TEXT,
 started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS thread_runs_thread ON thread_runs(thread_id,created_at DESC);
CREATE TABLE IF NOT EXISTS thread_messages (
 thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, seq BIGINT NOT NULL,
 run_id TEXT, message_id TEXT, author TEXT NOT NULL, payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(thread_id,seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS thread_messages_identity ON thread_messages(thread_id,message_id) WHERE message_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS thread_events (
 id BIGSERIAL PRIMARY KEY, thread_id TEXT NOT NULL, owner_login TEXT NOT NULL,
 kind TEXT NOT NULL, payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS thread_events_owner_id ON thread_events(owner_login,id);
CREATE TABLE IF NOT EXISTS thread_devices (
 id TEXT NOT NULL, owner_login TEXT NOT NULL, name TEXT NOT NULL,
 last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(id,owner_login)
);
"""


class PostgresRegistry:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: Any = None
        self._listener: Any = None
        self._condition = asyncio.Condition()

    async def initialize(self) -> None:
        if self._pool is not None:
            return
        if asyncpg is None:
            raise RuntimeError("asyncpg is required for the Postgres thread registry")
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        async with self._pool.acquire() as connection:
            await connection.execute(_SCHEMA_POSTGRES)
        self._listener = await asyncpg.connect(self.dsn)
        await self._listener.add_listener("thread_events", self._on_notify)

    def _on_notify(self, *_args: Any) -> None:
        async def wake() -> None:
            async with self._condition:
                self._condition.notify_all()

        asyncio.create_task(wake())

    async def close(self) -> None:
        if self._listener is not None:
            await self._listener.close()
            self._listener = None
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        await self.initialize()
        async with self._pool.acquire() as connection:
            yield connection

    async def _emit(
        self,
        connection: Any,
        thread_id: str,
        owner_login: str,
        kind: str,
        payload: Mapping[str, Any],
    ) -> int:
        event_id = await connection.fetchval(
            "INSERT INTO thread_events(thread_id,owner_login,kind,payload) VALUES($1,$2,$3,$4::jsonb) RETURNING id",
            thread_id,
            owner_login,
            kind,
            _json(payload),
        )
        await connection.execute("SELECT pg_notify($1,$2)", "thread_events", str(event_id))
        return int(event_id)

    async def create(self, value: ThreadCreate) -> ThreadRow:
        _validate_create(value)
        created = value.created_at or utcnow()
        updated = value.updated_at or created
        async with self._connection() as connection, connection.transaction():
            inserted = await connection.fetchrow(
                """INSERT INTO threads(
                     id,owner_login,owner_email,title,repo_full_name,branch,environment,device_id,
                     device_name,source,category,trigger_kind,automation_id,plan_status,model,effort,
                     sandbox_id,metadata,created_at,updated_at)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb,$19,$20)
                   ON CONFLICT(id) DO NOTHING RETURNING id""",
                value.id,
                value.owner_login,
                value.owner_email,
                value.title[:500],
                value.repo_full_name,
                value.branch,
                value.environment,
                value.device_id,
                value.device_name,
                value.source,
                value.category,
                value.trigger_kind,
                value.automation_id,
                value.plan_status,
                value.model,
                value.effort,
                value.sandbox_id,
                _json(value.metadata),
                created,
                updated,
            )
            row = await connection.fetchrow(f"{_THREAD_SELECT} WHERE t.id=$1", value.id)
            thread = _thread_from_row(row)
            if inserted:
                await self._emit(
                    connection, value.id, value.owner_login, "thread.created", thread.api_dict()
                )
        return thread

    async def get(self, thread_id: str) -> ThreadRow | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(f"{_THREAD_SELECT} WHERE t.id=$1", thread_id)
        return _thread_from_row(row) if row else None

    async def list(
        self,
        owner: str | None,
        *,
        resolved: bool | None = None,
        environment: str | None = None,
        source: str | None = None,
        status: str | None = None,
        q: str | None = None,
        scope: str = "all",
        automation_id: str | None = None,
        viewed: bool | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ThreadPage:
        if environment is not None and environment not in THREAD_ENVIRONMENTS:
            raise ValueError("invalid environment filter")
        if status is not None and status not in THREAD_STATUSES:
            raise ValueError("invalid status filter")
        if scope not in {"all", "interactive", "automation"}:
            raise ValueError("invalid thread scope")
        after = decode_cursor(cursor)
        clauses: list[str] = []
        values: list[Any] = []

        def bind(value: Any) -> str:
            values.append(value)
            return f"${len(values)}"

        if owner is not None:
            clauses.append(f"t.owner_login={bind(owner)}")
        if resolved is not None:
            clauses.append(f"t.resolved={bind(resolved)}")
        if environment is not None:
            clauses.append(f"t.environment={bind(environment)}")
        if source is not None:
            clauses.append(f"t.source={bind(source)}")
        if status is not None:
            clauses.append(f"t.status={bind(status)}")
        if q:
            clauses.append(f"t.title ILIKE {bind('%' + q + '%')}")
        if scope == "interactive":
            clauses.append("t.category<>'automation'")
        elif scope == "automation":
            clauses.append("t.category='automation'")
        if automation_id is not None:
            clauses.append(f"t.automation_id={bind(automation_id)}")
        if viewed is not None:
            operator = "=" if viewed else "<>"
            clauses.append(
                f"COALESCE(t.viewed_run_id,'') {operator} COALESCE((SELECT vr.id FROM thread_runs vr WHERE vr.thread_id=t.id AND vr.status='finished' ORDER BY vr.created_at DESC LIMIT 1),'')"
            )
        if after:
            stamp = bind(after[0])
            ident = bind(after[1])
            clauses.append(
                f"(t.updated_at < {stamp} OR (t.updated_at = {stamp} AND t.id < {ident}))"
            )
        bounded_limit = max(1, min(limit, 200))
        limit_arg = bind(bounded_limit + 1)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"{_THREAD_SELECT}{where} ORDER BY t.updated_at DESC,t.id DESC LIMIT {limit_arg}"
        async with self._connection() as connection:
            rows = await connection.fetch(sql, *values)
        has_more = len(rows) > bounded_limit
        items = [_thread_from_row(row) for row in rows[:bounded_limit]]
        return ThreadPage(
            items=items,
            cursor=_cursor(items[-1].updated_at, items[-1].id) if has_more and items else None,
            has_more=has_more,
        )

    async def transition(
        self,
        thread_id: str,
        run_id: str,
        status: RunStatus,
        *,
        environment: ThreadEnvironment | None = None,
        device_id: str | None = None,
        error: str | None = None,
        guard_run_id: str | None = None,
    ) -> ThreadRow:
        _validate_transition(status)
        now = utcnow()
        async with self._connection() as connection, connection.transaction():
            current = await connection.fetchrow(
                "SELECT * FROM threads WHERE id=$1 FOR UPDATE", thread_id
            )
            if current is None:
                raise KeyError(thread_id)
            if _transition_is_stale(
                str(current["status"]),
                current["status_run_id"],
                run_id,
                status,
                guard_run_id,
            ):
                row = await connection.fetchrow(f"{_THREAD_SELECT} WHERE t.id=$1", thread_id)
                return _thread_from_row(row)
            if not _transition_allowed(
                str(current["status"]), status, current["status_run_id"], run_id
            ):
                raise ValueError(f"invalid transition {current['status']} -> {status}")
            run_environment = environment or current["environment"]
            await connection.execute(
                """INSERT INTO thread_runs(id,thread_id,environment,device_id,status,error,started_at,finished_at,created_at)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,error=excluded.error,
                     started_at=COALESCE(thread_runs.started_at,excluded.started_at),finished_at=excluded.finished_at""",
                run_id,
                thread_id,
                run_environment,
                device_id,
                status,
                error,
                now if status == "running" else None,
                now if status in TERMINAL_STATUSES else None,
                now,
            )
            row = await connection.fetchrow(
                """UPDATE threads SET status=$1,status_run_id=$2,status_at=$3,updated_at=$3,
                     environment=COALESCE($4,environment),
                     device_id=CASE WHEN $4='cloud' THEN NULL ELSE COALESCE($5,device_id) END
                   WHERE id=$6 RETURNING *""",
                status,
                run_id,
                now,
                environment,
                device_id,
                thread_id,
            )
            selected = await connection.fetchrow(f"{_THREAD_SELECT} WHERE t.id=$1", thread_id)
            thread = _thread_from_row(selected or row)
            await self._emit(
                connection, thread_id, thread.owner_login, "thread.status", thread.api_dict()
            )
        return thread

    async def update_meta(self, thread_id: str, **fields: Any) -> ThreadRow:
        allowed = {
            "title",
            "repo_full_name",
            "branch",
            "environment",
            "device_id",
            "device_name",
            "source",
            "category",
            "trigger_kind",
            "automation_id",
            "plan_status",
            "model",
            "effort",
            "resolved",
            "resolved_at",
            "viewed_run_id",
            "sandbox_id",
            "git_checkpoint",
            "pr_state",
            "diff_stats",
            "metadata",
        }
        if set(fields) - allowed:
            raise ValueError("unsupported thread fields")
        if fields.get("environment") not in (None, *THREAD_ENVIRONMENTS):
            raise ValueError("invalid thread environment")
        values: list[Any] = []
        assignments: list[str] = []
        for key, value in fields.items():
            values.append(
                _json(value)
                if key in {"git_checkpoint", "pr_state", "diff_stats", "metadata"}
                and value is not None
                else value
            )
            suffix = (
                "::jsonb"
                if key in {"git_checkpoint", "pr_state", "diff_stats", "metadata"}
                and value is not None
                else ""
            )
            assignments.append(f"{key}=${len(values)}{suffix}")
        values.append(utcnow())
        assignments.append(f"updated_at=${len(values)}")
        values.append(thread_id)
        async with self._connection() as connection, connection.transaction():
            changed = await connection.fetchrow(
                f"UPDATE threads SET {','.join(assignments)} WHERE id=${len(values)} RETURNING id",
                *values,
            )
            if changed is None:
                raise KeyError(thread_id)
            selected = await connection.fetchrow(f"{_THREAD_SELECT} WHERE t.id=$1", thread_id)
            thread = _thread_from_row(selected)
            kind = "thread.handoff" if "environment" in fields else "thread.meta"
            await self._emit(connection, thread_id, thread.owner_login, kind, thread.api_dict())
        return thread

    async def delete(self, thread_id: str) -> bool:
        async with self._connection() as connection, connection.transaction():
            row = await connection.fetchrow(
                "SELECT owner_login FROM threads WHERE id=$1 FOR UPDATE", thread_id
            )
            if row is None:
                return False
            await self._emit(
                connection, thread_id, row["owner_login"], "thread.deleted", {"id": thread_id}
            )
            await connection.execute("DELETE FROM threads WHERE id=$1", thread_id)
        return True

    async def append_messages(
        self, thread_id: str, run_id: str | None, messages: Sequence[Mapping[str, Any]]
    ) -> int:
        if len(messages) > MAX_MESSAGE_BATCH:
            raise ValueError("message batch too large")
        async with self._connection() as connection, connection.transaction():
            owner = await connection.fetchval(
                "SELECT owner_login FROM threads WHERE id=$1 FOR UPDATE", thread_id
            )
            if owner is None:
                raise KeyError(thread_id)
            seq = int(
                await connection.fetchval(
                    "SELECT COALESCE(MAX(seq),0) FROM thread_messages WHERE thread_id=$1", thread_id
                )
            )
            appended = 0
            for message in messages:
                author, payload, message_id = _bounded_payload(message)
                if message_id and await connection.fetchval(
                    "SELECT 1 FROM thread_messages WHERE thread_id=$1 AND message_id=$2",
                    thread_id,
                    message_id,
                ):
                    continue
                seq += 1
                await connection.execute(
                    "INSERT INTO thread_messages(thread_id,seq,run_id,message_id,author,payload) VALUES($1,$2,$3,$4,$5,$6::jsonb)",
                    thread_id,
                    seq,
                    run_id,
                    message_id,
                    author,
                    payload,
                )
                appended += 1
            if appended:
                await connection.execute(
                    "UPDATE threads SET updated_at=now() WHERE id=$1", thread_id
                )
                await self._emit(
                    connection,
                    thread_id,
                    owner,
                    "thread.message",
                    {"thread_id": thread_id, "seq": seq},
                )
        return seq

    async def get_messages(
        self, thread_id: str, *, after_seq: int = 0
    ) -> builtins.list[dict[str, Any]]:
        async with self._connection() as connection:
            rows = await connection.fetch(
                "SELECT seq,run_id,author,payload,created_at FROM thread_messages WHERE thread_id=$1 AND seq>$2 ORDER BY seq LIMIT 1000",
                thread_id,
                max(0, after_seq),
            )
        return [
            {
                "seq": row["seq"],
                "run_id": row["run_id"],
                "author": row["author"],
                "payload": _json_payload(row["payload"]),
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ]

    async def events_since(
        self, cursor: int, owner: str | None, *, limit: int = 500
    ) -> builtins.list[ThreadEvent]:
        bounded = max(1, min(limit, 1000))
        async with self._connection() as connection:
            if owner is None:
                rows = await connection.fetch(
                    "SELECT * FROM thread_events WHERE id>$1 ORDER BY id LIMIT $2",
                    max(0, cursor),
                    bounded,
                )
            else:
                rows = await connection.fetch(
                    "SELECT * FROM thread_events WHERE id>$1 AND owner_login=$2 ORDER BY id LIMIT $3",
                    max(0, cursor),
                    owner,
                    bounded,
                )
        return [_event_from_row(row) for row in rows]

    async def wait_for_events(self, timeout: float = 15.0) -> None:
        try:
            async with self._condition:
                await asyncio.wait_for(self._condition.wait(), timeout)
        except TimeoutError:
            pass

    async def prune_events(self, *, older_than: datetime) -> int:
        async with self._connection() as connection:
            result = await connection.execute(
                "DELETE FROM thread_events WHERE created_at<$1", older_than
            )
        return int(result.rsplit(" ", 1)[-1])

    async def record_heartbeat(self, device_id: str, owner_login: str, device_name: str) -> None:
        async with self._connection() as connection:
            await connection.execute(
                """INSERT INTO thread_devices(id,owner_login,name,last_seen_at) VALUES($1,$2,$3,now())
                   ON CONFLICT(id,owner_login) DO UPDATE SET name=excluded.name,last_seen_at=excluded.last_seen_at""",
                device_id,
                owner_login,
                device_name[:255],
            )

    async def device(self, device_id: str, owner_login: str) -> dict[str, Any] | None:
        async with self._connection() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM thread_devices WHERE id=$1 AND owner_login=$2",
                device_id,
                owner_login,
            )
        return dict(row) if row else None


_registry: ThreadRegistry | None = None
_registry_lock = asyncio.Lock()


def _build_registry() -> ThreadRegistry:
    sqlite_path = os.environ.get("OPEN_SWE_REGISTRY_SQLITE_PATH")
    local_only = os.environ.get("OPEN_SWE_LOCAL_ONLY") == "1"
    local_dev = os.environ.get("LANGSMITH_LANGGRAPH_API_VARIANT") == "local_dev"
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URI")
    if local_only or local_dev or sqlite_path:
        path = sqlite_path or (
            ".langgraph_api/thread-registry.sqlite3" if local_dev else ":memory:"
        )
        return SqliteRegistry(path)
    if dsn:
        return PostgresRegistry(dsn)
    raise RuntimeError(
        "thread registry requires DATABASE_URL/POSTGRES_URI or OPEN_SWE_REGISTRY_SQLITE_PATH"
    )


async def get_thread_registry() -> ThreadRegistry:
    global _registry
    if _registry is None:
        async with _registry_lock:
            if _registry is None:
                _registry = _build_registry()
                await _registry.initialize()
    return _registry


async def initialize_thread_registry() -> ThreadRegistry:
    return await get_thread_registry()


async def close_thread_registry() -> None:
    global _registry
    async with _registry_lock:
        if _registry is not None:
            await _registry.close()
            _registry = None


def set_thread_registry_for_testing(registry: ThreadRegistry | None) -> None:
    global _registry
    _registry = registry


def thread_create_from_metadata(
    thread_id: str,
    owner_login: str,
    metadata: Mapping[str, Any],
    *,
    owner_email: str | None = None,
) -> ThreadCreate:
    repo = metadata.get("repo")
    repo_full_name = None
    if (
        isinstance(repo, dict)
        and isinstance(repo.get("owner"), str)
        and isinstance(repo.get("name"), str)
    ):
        repo_full_name = f"{repo['owner']}/{repo['name']}"
    elif isinstance(metadata.get("repo_full_name"), str):
        repo_full_name = metadata["repo_full_name"]
    return ThreadCreate(
        id=thread_id,
        owner_login=owner_login,
        owner_email=owner_email,
        title=str(metadata.get("title") or "Untitled agent")[:500],
        repo_full_name=repo_full_name,
        branch=cast(str | None, metadata.get("branch_name") or metadata.get("base_branch")),
        environment="local" if metadata.get("execution_environment") == "local" else "cloud",
        device_id=cast(str | None, metadata.get("device_id")),
        device_name=cast(str | None, metadata.get("device_name")),
        source=str(metadata.get("source") or "dashboard"),
        category=str(metadata.get("thread_category") or "interactive"),
        trigger_kind=str(metadata.get("trigger_kind") or "user"),
        automation_id=cast(str | None, metadata.get("schedule_id")),
        plan_status=cast(str | None, metadata.get("plan_status")),
        model=cast(str | None, metadata.get("model")),
        effort=cast(str | None, metadata.get("effort")),
        sandbox_id=cast(str | None, metadata.get("sandbox_id")),
        metadata=dict(metadata),
    )
