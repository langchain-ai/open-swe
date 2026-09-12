import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.config import ENV
from agent.database import postgres

logger = logging.getLogger(__name__)

_WORKSPACE_ID: UUID | None = None
analytics_uri = postgres.uri
configured = postgres.configured
engine = postgres.engine
connection = postgres.connection
transaction = postgres.transaction


def workspace_id() -> UUID:
    if _WORKSPACE_ID is None:
        raise RuntimeError("analytics migrations have not completed")
    return _WORKSPACE_ID


async def collection_started_at(conn: AsyncConnection) -> datetime | None:
    return await conn.scalar(text("SELECT collection_started_at FROM deployment_metadata"))


async def record_capture(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            "UPDATE deployment_metadata SET collection_started_at = clock_timestamp() "
            "WHERE collection_started_at IS NULL"
        )
    )


async def activate_reporting() -> None:
    """Start a fresh reporting period once, when the SQL reporting path is enabled."""
    if not configured():
        return
    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE deployment_metadata SET reporting_cutover_at = clock_timestamp() "
                "WHERE reporting_cutover_at IS NULL"
            )
        )


async def reporting_metadata(conn: AsyncConnection) -> dict[str, Any]:
    result = await conn.execute(
        text(
            "SELECT collection_started_at, last_processed_at, reporting_cutover_at, "
            "EXISTS (SELECT 1 FROM outbox WHERE workspace_id = d.workspace_id "
            "AND state IN ('pending', 'delivering')) AS has_pending_events, "
            "EXISTS (SELECT 1 FROM outbox WHERE workspace_id = d.workspace_id "
            "AND state = 'dead_letter') AS has_failed_events "
            "FROM deployment_metadata d"
        )
    )
    row = result.mappings().one()
    return {
        "reporting_cutover_at": row["reporting_cutover_at"].isoformat()
        if row["reporting_cutover_at"]
        else None,
        "collection_started_at": row["collection_started_at"].isoformat()
        if row["collection_started_at"]
        else None,
        "last_processed_at": row["last_processed_at"].isoformat()
        if row["last_processed_at"]
        else None,
        "data_source": "event_projections",
        "completeness": "observed_events_only" if row["collection_started_at"] else "not_started",
        "has_pending_events": row["has_pending_events"],
        "has_failed_events": row["has_failed_events"],
    }


async def migrate() -> None:
    global _WORKSPACE_ID
    if not configured():
        logger.info(
            "Analytics disabled",
            extra={"analytics_database_setting": "POSTGRES_URI", "analytics_reason": "unset"},
        )
        return
    logger.info(
        "Initializing database",
        extra={"database_setting": "POSTGRES_URI", "database_schema": postgres.SCHEMA},
    )
    await postgres.migrate()
    async with connection() as conn:
        persisted_workspace = await conn.scalar(
            text("SELECT workspace_id FROM deployment_metadata")
        )
    if persisted_workspace is None:
        raise RuntimeError("analytics deployment metadata is missing")
    _WORKSPACE_ID = persisted_workspace
    logger.info(
        "Database initialized",
        extra={
            "database_setting": "POSTGRES_URI",
            "database_schema": postgres.SCHEMA,
            "analytics_workspace_id": str(persisted_workspace),
        },
    )


async def readiness() -> dict[str, Any]:
    try:
        if not configured():
            return {"configured": False, "ready": False, "reason": "not configured"}
        async with asyncio.timeout(ENV.ANALYTICS_HEALTH_TIMEOUT_SECONDS.get_int(3)):
            async with connection() as conn:
                event_count = await conn.scalar(text("SELECT count(*) FROM events WHERE false"))
                pending = await conn.scalar(
                    text("SELECT count(*) FROM outbox WHERE state = 'pending'")
                )
                dead_letters = await conn.scalar(
                    text("SELECT count(*) FROM outbox WHERE state = 'dead_letter'")
                )
                metadata = await reporting_metadata(conn)
        return {
            "configured": True,
            "ready": event_count == 0,
            "pending_outbox": int(pending or 0),
            "dead_letters": int(dead_letters or 0),
            "workspace_id": str(workspace_id()),
            **metadata,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Analytics readiness check failed", exc_info=True)
        return {"configured": True, "ready": False, "reason": type(exc).__name__}


async def close() -> None:
    global _WORKSPACE_ID
    await postgres.close()
    _WORKSPACE_ID = None
