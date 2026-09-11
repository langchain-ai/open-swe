"""SQLAlchemy async access to analytics tables in the deployment PostgreSQL database."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from agent.config import ENV

logger = logging.getLogger(__name__)

_ENGINE: AsyncEngine | None = None
_ENGINE_URI: str | None = None
_WORKSPACE_ID: UUID | None = None
_MIGRATION_DIR = Path(__file__).with_name("migrations")
_MIGRATION_LOCK = 557314367248862439


def analytics_uri() -> str | None:
    value = ENV.POSTGRES_URI.optional()
    if value is None:
        return None
    if value.startswith("postgres://"):
        value = "postgresql://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        value = "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if not value.startswith("postgresql+asyncpg://"):
        raise ValueError("analytics PostgreSQL URI must use a PostgreSQL scheme")
    url = make_url(value)
    if "sslmode" in url.query:
        if "ssl" in url.query:
            raise ValueError("analytics PostgreSQL URI must specify only one SSL mode")
        url = url.update_query_dict({"ssl": url.query["sslmode"]}).difference_update_query(
            ["sslmode"]
        )
    return url.render_as_string(hide_password=False)


def configured() -> bool:
    return analytics_uri() is not None


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


async def reporting_metadata(conn: AsyncConnection) -> dict[str, Any]:
    result = await conn.execute(
        text(
            "SELECT collection_started_at, last_processed_at, "
            "EXISTS (SELECT 1 FROM outbox WHERE workspace_id = d.workspace_id "
            "AND state IN ('pending', 'delivering')) AS has_pending_events, "
            "EXISTS (SELECT 1 FROM outbox WHERE workspace_id = d.workspace_id "
            "AND state = 'dead_letter') AS has_failed_events "
            "FROM deployment_metadata d"
        )
    )
    row = result.mappings().one()
    return {
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


def engine() -> AsyncEngine:
    global _ENGINE, _ENGINE_URI
    uri = analytics_uri()
    if uri is None:
        raise RuntimeError("analytics PostgreSQL is not configured")
    if _ENGINE is None or _ENGINE_URI != uri:
        _ENGINE = create_async_engine(
            uri,
            pool_pre_ping=True,
            pool_size=ENV.ANALYTICS_POOL_SIZE.get_int(5),
            max_overflow=ENV.ANALYTICS_POOL_OVERFLOW.get_int(5),
            pool_timeout=ENV.ANALYTICS_POOL_TIMEOUT_SECONDS.get_int(5),
            connect_args={"server_settings": {"application_name": "open-swe-analytics"}},
        )
        _ENGINE_URI = uri
    return _ENGINE


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    async with engine().connect() as conn:
        await conn.execute(text("SET search_path TO open_swe_analytics, public"))
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    async with engine().begin() as conn:
        await conn.execute(text("SET LOCAL search_path TO open_swe_analytics, public"))
        yield conn


async def migrate() -> None:
    global _WORKSPACE_ID
    if not configured():
        logger.info(
            "Analytics disabled",
            extra={"analytics_database_setting": "POSTGRES_URI", "analytics_reason": "unset"},
        )
        return
    logger.info(
        "Initializing analytics database",
        extra={
            "analytics_database_setting": "POSTGRES_URI",
            "analytics_schema": "open_swe_analytics",
        },
    )
    async with engine().begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK})
        await _run_script(conn, "CREATE SCHEMA IF NOT EXISTS open_swe_analytics")
        await _run_script(
            conn,
            "CREATE TABLE IF NOT EXISTS open_swe_analytics.schema_migrations "
            "(version integer PRIMARY KEY, applied_at timestamptz NOT NULL "
            "DEFAULT clock_timestamp())",
        )
        for path in sorted(_MIGRATION_DIR.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            applied = await conn.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM open_swe_analytics.schema_migrations "
                    "WHERE version = :version)"
                ),
                {"version": version},
            )
            if applied:
                continue
            await _run_script(conn, path.read_text())
            await conn.execute(
                text(
                    "INSERT INTO open_swe_analytics.schema_migrations (version) VALUES (:version) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"version": version},
            )

        persisted_workspace = await conn.scalar(
            text("SELECT workspace_id FROM open_swe_analytics.deployment_metadata")
        )
        if persisted_workspace is None:
            raise RuntimeError("analytics deployment metadata is missing")
    _WORKSPACE_ID = persisted_workspace
    logger.info(
        "Analytics database initialized",
        extra={
            "analytics_database_setting": "POSTGRES_URI",
            "analytics_schema": "open_swe_analytics",
            "analytics_workspace_id": str(persisted_workspace),
        },
    )


async def _run_script(conn: AsyncConnection, script: str) -> None:
    """Run trusted multi-statement DDL through asyncpg's simple query protocol."""
    raw = await conn.get_raw_connection()
    driver_connection = raw.driver_connection
    if driver_connection is None:
        raise RuntimeError("analytics migration requires an asyncpg driver connection")
    await driver_connection.execute(script)


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
    global _ENGINE, _ENGINE_URI, _WORKSPACE_ID
    if _ENGINE is not None:
        await _ENGINE.dispose()
    _ENGINE = None
    _ENGINE_URI = None
    _WORKSPACE_ID = None
