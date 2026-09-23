"""The queue a CLI answers a cloud run's sandbox requests through.

The graph worker and the HTTP handler holding the CLI's long poll are usually
different processes, so everything they agree on is in Postgres: a request moves
``pending -> claimed -> done | failed`` and nothing else, the claim takes
``FOR UPDATE SKIP LOCKED`` so two claimers never carry the same request, and the
transition to a finished status is conditional on the request not being finished
already — which is what makes a request answered at most once.

Every transition notifies inside its own transaction, and also publishes in
process: that is an optimisation for the common single-replica case and is
deliberately the same code path, so a missing publish only ever costs latency.

A bridge is alive while its heartbeat is recent. A machine that goes away
mid-run simply stops heartbeating, and the prune sweep turns that into a closed
bridge with failed requests rather than waiters that never return.
"""

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, Self

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.bridge import listener
from agent.bridge.constants import (
    ALIVE_THRESHOLD_SECONDS,
    CHANNEL,
    CLOSED_EVENT,
    REQUEST_EVENT,
    RESULT_EVENT,
    SANDBOX_ID_PREFIX,
)
from agent.bridge.protocol import BridgeMethod, JsonObject
from agent.database import postgres

logger = logging.getLogger(__name__)

BridgeRequestStatus = Literal["pending", "claimed", "done", "failed"]
CompleteOutcome = Literal["completed", "already_finished", "missing"]

CLOSED_ERROR = "bridge closed"
DISCONNECTED_ERROR = "bridge disconnected"

_BRIDGE_COLUMNS = (
    "bridge_id, owner_login, hostname, root_path, label, created_at, last_heartbeat_at, closed_at"
)


class BridgeUnavailableError(RuntimeError):
    """The bridge a run is bound to is closed, stale, or gone.

    Raised on the graph side, where it means the machine running the CLI is not answering
    this run — never that the bridge should be replaced by anything else.
    """

    def __init__(self, bridge_id: str, cause: str) -> None:
        self.bridge_id = bridge_id
        super().__init__(f"Sandbox bridge {bridge_id} is unavailable: {cause}")


class Bridge(BaseModel):
    """One CLI's registration, as the ``sandbox_bridge`` row holds it."""

    bridge_id: str
    owner_login: str
    hostname: str
    root_path: str
    label: str | None = None
    created_at: datetime
    last_heartbeat_at: datetime
    closed_at: datetime | None = None

    @classmethod
    def of(cls, row: RowMapping) -> Self:
        return cls.model_validate(dict(row))

    @classmethod
    def sandbox_id_for(cls, bridge_id: str) -> str:
        return f"{SANDBOX_ID_PREFIX}{bridge_id}"

    @classmethod
    def bridge_id_of(cls, sandbox_id: str | None) -> str | None:
        """The bridge a ``sandbox_id`` names, or ``None`` when it names a real box."""
        if not sandbox_id or not sandbox_id.startswith(SANDBOX_ID_PREFIX):
            return None
        return sandbox_id.removeprefix(SANDBOX_ID_PREFIX) or None

    @property
    def sandbox_id(self) -> str:
        return self.sandbox_id_for(self.bridge_id)

    @property
    def is_alive(self) -> bool:
        if self.closed_at is not None:
            return False
        quiet_for = datetime.now(UTC) - self.last_heartbeat_at
        return quiet_for.total_seconds() <= ALIVE_THRESHOLD_SECONDS


class SandboxBridgeBinding(BaseModel):
    """The thread metadata that binds a thread to one machine's bridge.

    ``sandbox_id`` is the same key a hosted sandbox uses, so every reader that
    already resolves a thread's sandbox from metadata resolves this one too.
    """

    sandbox_id: str
    sandbox_kind: Literal["bridge"] = "bridge"

    @classmethod
    def of(cls, bridge: Bridge) -> Self:
        return cls(sandbox_id=bridge.sandbox_id)

    def dump(self) -> dict[str, str]:
        return self.model_dump(mode="json")


class ClaimedRequest(BaseModel):
    """One request handed to the CLI, in the shape the wire contract gives it."""

    request_id: str
    method: str
    params: JsonObject


class BridgeRequestOutcome(BaseModel):
    status: BridgeRequestStatus
    result: JsonObject | None = None
    error: str | None = None

    @property
    def finished(self) -> bool:
        return self.status in {"done", "failed"}


class BridgeStore:
    """Every read and write of the bridge tables, and the notifications they carry."""

    @staticmethod
    async def _notify(conn: AsyncConnection, bridge_id: str, request_id: str, event: str) -> None:
        await conn.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": CHANNEL, "payload": f"{bridge_id}:{request_id}:{event}"},
        )

    @staticmethod
    async def _rows(
        conn: AsyncConnection, statement: str, parameters: Mapping[str, object]
    ) -> list[RowMapping]:
        result = await conn.execute(text(statement), parameters)
        return list(result.mappings())

    @classmethod
    async def register(
        cls,
        *,
        owner_login: str,
        hostname: str,
        root_path: str,
        label: str | None,
        bridge_id: str | None,
    ) -> Bridge | None:
        """Open a new bridge, or reopen one the caller already owns.

        A reopen is how a restarted CLI picks its thread back up, so it also
        re-queues whatever the previous connection claimed and never answered:
        those requests still have a waiter on the graph side.
        """
        if bridge_id is None:
            async with postgres.transaction() as conn:
                rows = await cls._rows(
                    conn,
                    f"""
                    INSERT INTO sandbox_bridge
                        (bridge_id, owner_login, hostname, root_path, label)
                    VALUES (:bridge_id, :owner_login, :hostname, :root_path, :label)
                    RETURNING {_BRIDGE_COLUMNS}
                    """,
                    {
                        "bridge_id": uuid.uuid4().hex,
                        "owner_login": owner_login,
                        "hostname": hostname,
                        "root_path": root_path,
                        "label": label,
                    },
                )
            return Bridge.of(rows[0])

        async with postgres.transaction() as conn:
            rows = await cls._rows(
                conn,
                f"""
                UPDATE sandbox_bridge
                SET hostname = :hostname,
                    root_path = :root_path,
                    label = :label,
                    last_heartbeat_at = clock_timestamp(),
                    closed_at = NULL
                WHERE bridge_id = :bridge_id AND owner_login = :owner_login
                RETURNING {_BRIDGE_COLUMNS}
                """,
                {
                    "bridge_id": bridge_id,
                    "owner_login": owner_login,
                    "hostname": hostname,
                    "root_path": root_path,
                    "label": label,
                },
            )
            if not rows:
                return None
            requeued = await cls._rows(
                conn,
                """
                UPDATE sandbox_bridge_request
                SET status = 'pending', claimed_at = NULL
                WHERE bridge_id = :bridge_id AND status = 'claimed'
                RETURNING request_id
                """,
                {"bridge_id": bridge_id},
            )
            if requeued:
                await cls._notify(conn, bridge_id, "", REQUEST_EVENT)
        if requeued:
            listener.publish(bridge_id, "", REQUEST_EVENT)
            logger.info(
                "Re-queued unanswered sandbox bridge requests",
                extra={"bridge_id": bridge_id, "requeued_requests": len(requeued)},
            )
        return Bridge.of(rows[0])

    @classmethod
    async def load(cls, bridge_id: str, *, owner_login: str | None = None) -> Bridge | None:
        """The bridge, optionally pinned to its owner.

        The graph side has no login to pin to — it reaches a bridge only through
        the ``sandbox_id`` the owner's own request already stamped on the thread.
        """
        async with postgres.read_only_transaction() as conn:
            rows = await cls._rows(
                conn,
                f"""
                SELECT {_BRIDGE_COLUMNS} FROM sandbox_bridge
                WHERE bridge_id = :bridge_id
                  AND (CAST(:owner_login AS text) IS NULL OR owner_login = :owner_login)
                """,
                {"bridge_id": bridge_id, "owner_login": owner_login},
            )
        return Bridge.of(rows[0]) if rows else None

    @classmethod
    async def require_open(cls, bridge_id: str, *, owner_login: str) -> Bridge:
        """The caller's open bridge, or an HTTP error that leaks no other user's ids."""
        if not postgres.configured():
            raise HTTPException(503, "sandbox bridges require PostgreSQL")
        bridge = await cls.load(bridge_id, owner_login=owner_login)
        if bridge is None:
            raise HTTPException(404, "sandbox bridge not found")
        if not bridge.is_alive:
            raise HTTPException(409, "sandbox bridge is not connected")
        return bridge

    @classmethod
    async def heartbeat(cls, bridge_id: str, *, owner_login: str) -> bool:
        async with postgres.transaction() as conn:
            result = await conn.execute(
                text(
                    """
                    UPDATE sandbox_bridge SET last_heartbeat_at = clock_timestamp()
                    WHERE bridge_id = :bridge_id
                      AND owner_login = :owner_login
                      AND closed_at IS NULL
                    """
                ),
                {"bridge_id": bridge_id, "owner_login": owner_login},
            )
        return result.rowcount > 0

    @classmethod
    async def claim(cls, bridge_id: str, *, limit: int) -> list[ClaimedRequest]:
        """Take up to ``limit`` pending requests, exclusively.

        ``SKIP LOCKED`` is what makes two claimers of one bridge — a reconnect
        racing the poll it replaces — split the queue instead of both running
        the same command.
        """
        async with postgres.transaction() as conn:
            rows = await cls._rows(
                conn,
                """
                WITH claimable AS (
                    SELECT request_id FROM sandbox_bridge_request
                    WHERE bridge_id = :bridge_id AND status = 'pending'
                    ORDER BY created_at, request_id
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE sandbox_bridge_request AS request
                SET status = 'claimed', claimed_at = clock_timestamp()
                FROM claimable
                WHERE request.request_id = claimable.request_id
                RETURNING request.request_id, request.method, request.params,
                          request.created_at
                """,
                {"bridge_id": bridge_id, "limit": limit},
            )
        ordered = sorted(rows, key=lambda row: (row["created_at"], row["request_id"]))
        return [
            ClaimedRequest(request_id=row["request_id"], method=row["method"], params=row["params"])
            for row in ordered
        ]

    @classmethod
    async def enqueue(cls, bridge_id: str, *, method: BridgeMethod, params: JsonObject) -> str:
        """Queue one request for a live bridge and wake whoever is polling it."""
        request_id = uuid.uuid4().hex
        async with postgres.transaction() as conn:
            alive = await cls._rows(
                conn,
                """
                SELECT bridge_id FROM sandbox_bridge
                WHERE bridge_id = :bridge_id
                  AND closed_at IS NULL
                  AND last_heartbeat_at > clock_timestamp()
                      - make_interval(secs => :threshold)
                """,
                {"bridge_id": bridge_id, "threshold": ALIVE_THRESHOLD_SECONDS},
            )
            if not alive:
                raise BridgeUnavailableError(bridge_id, DISCONNECTED_ERROR)
            await conn.execute(
                text(
                    """
                    INSERT INTO sandbox_bridge_request
                        (request_id, bridge_id, method, params, status)
                    VALUES (:request_id, :bridge_id, :method, CAST(:params AS jsonb), 'pending')
                    """
                ),
                {
                    "request_id": request_id,
                    "bridge_id": bridge_id,
                    "method": method,
                    "params": json.dumps(params),
                },
            )
            await cls._notify(conn, bridge_id, request_id, REQUEST_EVENT)
        listener.publish(bridge_id, request_id, REQUEST_EVENT)
        return request_id

    @classmethod
    async def complete(
        cls,
        bridge_id: str,
        request_id: str,
        *,
        result: JsonObject | None = None,
        error: str | None = None,
    ) -> CompleteOutcome:
        """Settle a request that is not settled yet, and wake its waiter."""
        status: BridgeRequestStatus = "failed" if error is not None else "done"
        async with postgres.transaction() as conn:
            updated = await conn.execute(
                text(
                    """
                    UPDATE sandbox_bridge_request
                    SET status = :status,
                        result = CAST(:result AS jsonb),
                        error = :error,
                        finished_at = clock_timestamp()
                    WHERE request_id = :request_id
                      AND bridge_id = :bridge_id
                      AND status IN ('pending', 'claimed')
                    """
                ),
                {
                    "status": status,
                    "result": json.dumps(result) if result is not None else None,
                    "error": error,
                    "request_id": request_id,
                    "bridge_id": bridge_id,
                },
            )
            if updated.rowcount == 0:
                existing = await cls._rows(
                    conn,
                    """
                    SELECT request_id FROM sandbox_bridge_request
                    WHERE request_id = :request_id AND bridge_id = :bridge_id
                    """,
                    {"request_id": request_id, "bridge_id": bridge_id},
                )
                return "already_finished" if existing else "missing"
            await cls._notify(conn, bridge_id, request_id, RESULT_EVENT)
        listener.publish(bridge_id, request_id, RESULT_EVENT)
        return "completed"

    @classmethod
    async def outcome(cls, bridge_id: str, request_id: str) -> BridgeRequestOutcome | None:
        async with postgres.read_only_transaction() as conn:
            rows = await cls._rows(
                conn,
                """
                SELECT status, result, error FROM sandbox_bridge_request
                WHERE request_id = :request_id AND bridge_id = :bridge_id
                """,
                {"request_id": request_id, "bridge_id": bridge_id},
            )
        return BridgeRequestOutcome.model_validate(dict(rows[0])) if rows else None

    @classmethod
    async def close(cls, bridge_id: str, *, owner_login: str) -> bool:
        """Close the caller's bridge and fail everything it left unanswered.

        Idempotent: closing an already-closed bridge still reports success, so a
        CLI that shuts down twice does not have to care which call won.
        """
        async with postgres.transaction() as conn:
            owned = await cls._rows(
                conn,
                """
                SELECT bridge_id FROM sandbox_bridge
                WHERE bridge_id = :bridge_id AND owner_login = :owner_login
                """,
                {"bridge_id": bridge_id, "owner_login": owner_login},
            )
            if not owned:
                return False
            await conn.execute(
                text(
                    """
                    UPDATE sandbox_bridge SET closed_at = clock_timestamp()
                    WHERE bridge_id = :bridge_id AND closed_at IS NULL
                    """
                ),
                {"bridge_id": bridge_id},
            )
            failed = await cls._fail_unanswered(conn, [bridge_id], CLOSED_ERROR)
        cls._publish_closed({bridge_id: failed.get(bridge_id, [])})
        return True

    @classmethod
    async def prune_stale(cls) -> int:
        """Close bridges whose heartbeat stopped, failing what they never answered."""
        async with postgres.transaction() as conn:
            rows = await cls._rows(
                conn,
                """
                UPDATE sandbox_bridge SET closed_at = clock_timestamp()
                WHERE closed_at IS NULL
                  AND last_heartbeat_at
                      < clock_timestamp() - make_interval(secs => :threshold)
                RETURNING bridge_id
                """,
                {"threshold": ALIVE_THRESHOLD_SECONDS},
            )
            bridge_ids = [row["bridge_id"] for row in rows]
            if not bridge_ids:
                return 0
            failed = await cls._fail_unanswered(conn, bridge_ids, DISCONNECTED_ERROR)
        cls._publish_closed({bridge_id: failed.get(bridge_id, []) for bridge_id in bridge_ids})
        return len(bridge_ids)

    @classmethod
    async def _fail_unanswered(
        cls, conn: AsyncConnection, bridge_ids: Sequence[str], error: str
    ) -> dict[str, list[str]]:
        rows = await cls._rows(
            conn,
            """
            UPDATE sandbox_bridge_request
            SET status = 'failed', error = :error, finished_at = clock_timestamp()
            WHERE bridge_id = ANY(CAST(:bridge_ids AS text[]))
              AND status IN ('pending', 'claimed')
            RETURNING bridge_id, request_id
            """,
            {"error": error, "bridge_ids": list(bridge_ids)},
        )
        failed: dict[str, list[str]] = {}
        for row in rows:
            failed.setdefault(row["bridge_id"], []).append(row["request_id"])
        for bridge_id in bridge_ids:
            for request_id in failed.get(bridge_id, []):
                await cls._notify(conn, bridge_id, request_id, RESULT_EVENT)
            await cls._notify(conn, bridge_id, "", CLOSED_EVENT)
        return failed

    @staticmethod
    def _publish_closed(failed: Mapping[str, Sequence[str]]) -> None:
        for bridge_id, request_ids in failed.items():
            for request_id in request_ids:
                listener.publish(bridge_id, request_id, RESULT_EVENT)
            listener.publish(bridge_id, "", CLOSED_EVENT)
