"""SQLAlchemy async access to the dedicated analytics PostgreSQL datastore."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from agent.config import ENV

logger = logging.getLogger(__name__)

_ENGINE: AsyncEngine | None = None
_ENGINE_URI: str | None = None
_MIGRATION_DIR = Path(__file__).with_name("migrations")
_MIGRATION_LOCK = 557314367248862439


def analytics_uri() -> str | None:
    value = ENV.ANALYTICS_POSTGRES_URI.optional() or ENV.POSTGRES_URI.optional()
    if value is None:
        return None
    if value.startswith("postgres://"):
        value = "postgresql://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        value = "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if not value.startswith("postgresql+asyncpg://"):
        raise ValueError("analytics PostgreSQL URI must use a PostgreSQL scheme")
    return value


def configured() -> bool:
    return analytics_uri() is not None and ENV.ANALYTICS_WORKSPACE_ID.optional() is not None


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
    if not configured():
        return
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


async def _run_script(conn: AsyncConnection, script: str) -> None:
    """Run trusted multi-statement DDL through asyncpg's simple query protocol."""
    raw = await conn.get_raw_connection()
    driver_connection = raw.driver_connection
    if driver_connection is None:
        raise RuntimeError("analytics migration requires an asyncpg driver connection")
    await driver_connection.execute(script)


async def readiness() -> dict[str, Any]:
    if not configured():
        return {"configured": False, "ready": False, "reason": "not configured"}
    try:
        async with asyncio.timeout(ENV.ANALYTICS_HEALTH_TIMEOUT_SECONDS.get_int(3)):
            async with connection() as conn:
                event_count = await conn.scalar(text("SELECT count(*) FROM events WHERE false"))
                pending = await conn.scalar(
                    text("SELECT count(*) FROM outbox WHERE state = 'pending'")
                )
                dead_letters = await conn.scalar(
                    text("SELECT count(*) FROM outbox WHERE state = 'dead_letter'")
                )
        return {
            "configured": True,
            "ready": event_count == 0,
            "pending_outbox": int(pending or 0),
            "dead_letters": int(dead_letters or 0),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Analytics readiness check failed", exc_info=True)
        return {"configured": True, "ready": False, "reason": type(exc).__name__}


async def close() -> None:
    global _ENGINE, _ENGINE_URI
    if _ENGINE is not None:
        await _ENGINE.dispose()
    _ENGINE = None
    _ENGINE_URI = None
