"""SQLAlchemy async access to analytics tables in the deployment PostgreSQL database."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from operator import attrgetter
from pathlib import Path
from typing import Any
from uuid import UUID

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from agent.config import ENV

logger = logging.getLogger(__name__)

_ENGINE: AsyncEngine | None = None
_ENGINE_URI: str | None = None
_WORKSPACE_ID: UUID | None = None
_MIGRATION_DIR = Path(__file__).with_name("migrations")
_MIGRATION_LOCK = 557314367248862439
_SCHEMA = "open_swe"
_MIGRATIONS: ScriptDirectory | None = None


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
        sslmode = url.query["sslmode"]
        if not isinstance(sslmode, str):
            raise ValueError("analytics PostgreSQL URI must specify only one SSL mode")
        url = url.update_query_dict({"ssl": sslmode}).difference_update_query(["sslmode"])
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
        await conn.execute(text(f"SET search_path TO {_SCHEMA}, public"))
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    async with engine().begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {_SCHEMA}, public"))
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
        extra={"analytics_database_setting": "POSTGRES_URI", "analytics_schema": _SCHEMA},
    )
    migrations = await asyncio.to_thread(_load_migrations)
    async with engine().begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK})
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}"))
        await conn.run_sync(_upgrade, migrations)
        persisted_workspace = await conn.scalar(
            text(f"SELECT workspace_id FROM {_SCHEMA}.deployment_metadata")
        )
        if persisted_workspace is None:
            raise RuntimeError("analytics deployment metadata is missing")
    _WORKSPACE_ID = persisted_workspace
    logger.info(
        "Analytics database initialized",
        extra={
            "analytics_database_setting": "POSTGRES_URI",
            "analytics_schema": _SCHEMA,
            "analytics_workspace_id": str(persisted_workspace),
        },
    )


def _load_migrations() -> ScriptDirectory:
    global _MIGRATIONS
    if _MIGRATIONS is None:
        _MIGRATIONS = ScriptDirectory(str(_MIGRATION_DIR))
        list(_MIGRATIONS.walk_revisions())
    return _MIGRATIONS


def _upgrade(
    conn: Connection,
    migrations: ScriptDirectory,
    schema: str = _SCHEMA,
    revision: str = "head",
) -> None:
    conn.exec_driver_sql(f"SET LOCAL search_path TO {schema}, public")
    upgrade_revisions = attrgetter("_upgrade_revs")(migrations)
    context = MigrationContext.configure(
        connection=conn,
        opts={
            "fn": lambda current, _: upgrade_revisions(revision, current),
            "version_table_schema": schema,
            "transaction_per_migration": True,
            "analytics_schema": schema,
        },
    )
    with Operations.context(context):
        context.run_migrations()


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
