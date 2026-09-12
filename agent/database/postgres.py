import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from operator import attrgetter
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from agent.config import ENV

_ENGINE: AsyncEngine | None = None
_ENGINE_URI: str | None = None
_MIGRATIONS: ScriptDirectory | None = None
SCHEMA = "open_swe"
MIGRATION_DIR = Path(__file__).with_name("migrations")
MIGRATION_LOCK = 557314367248862439


def uri() -> str | None:
    value = ENV.POSTGRES_URI.optional()
    if value is None:
        return None
    if value.startswith("postgres://"):
        value = "postgresql://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        value = "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if not value.startswith("postgresql+asyncpg://"):
        raise ValueError("PostgreSQL URI must use a PostgreSQL scheme")
    url = make_url(value)
    if "sslmode" in url.query:
        if "ssl" in url.query:
            raise ValueError("PostgreSQL URI must specify only one SSL mode")
        sslmode = url.query["sslmode"]
        if not isinstance(sslmode, str):
            raise ValueError("PostgreSQL URI must specify only one SSL mode")
        url = url.update_query_dict({"ssl": sslmode}).difference_update_query(["sslmode"])
    return url.render_as_string(hide_password=False)


def configured() -> bool:
    return uri() is not None


def engine() -> AsyncEngine:
    global _ENGINE, _ENGINE_URI
    database_uri = uri()
    if database_uri is None:
        raise RuntimeError("PostgreSQL is not configured")
    if _ENGINE is None or _ENGINE_URI != database_uri:
        _ENGINE = create_async_engine(
            database_uri,
            pool_pre_ping=True,
            pool_size=ENV.ANALYTICS_POOL_SIZE.get_int(5),
            max_overflow=ENV.ANALYTICS_POOL_OVERFLOW.get_int(5),
            pool_timeout=ENV.ANALYTICS_POOL_TIMEOUT_SECONDS.get_int(5),
            connect_args={"server_settings": {"application_name": "open-swe"}},
        )
        _ENGINE_URI = database_uri
    return _ENGINE


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    async with engine().connect() as conn:
        await conn.execute(text(f"SET search_path TO {SCHEMA}, public"))
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    async with engine().begin() as conn:
        await conn.execute(text(f"SET LOCAL search_path TO {SCHEMA}, public"))
        yield conn


async def migrate() -> None:
    migrations = await asyncio.to_thread(load_migrations)
    async with engine().begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        await conn.run_sync(upgrade, migrations)


def load_migrations() -> ScriptDirectory:
    global _MIGRATIONS
    if _MIGRATIONS is None:
        _MIGRATIONS = ScriptDirectory(str(MIGRATION_DIR))
        list(_MIGRATIONS.walk_revisions())
    return _MIGRATIONS


def upgrade(
    conn: Connection,
    migrations: ScriptDirectory,
    schema: str = SCHEMA,
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
        },
    )
    with Operations.context(context):
        context.run_migrations()


async def close() -> None:
    global _ENGINE, _ENGINE_URI
    if _ENGINE is not None:
        await _ENGINE.dispose()
    _ENGINE = None
    _ENGINE_URI = None
