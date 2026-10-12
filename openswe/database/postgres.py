import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from operator import attrgetter
from pathlib import Path
from time import perf_counter

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    Connection,
    ExceptionContext,
    ExecutionContext,
    bindparam,
    event,
    make_url,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from openswe.config import ENV

logger = logging.getLogger(__name__)

_ENGINE: AsyncEngine | None = None
_ENGINE_URI: str | None = None
_MIGRATIONS: ScriptDirectory | None = None
SCHEMA = "open_swe"
MIGRATION_DIR = Path(__file__).with_name("migrations")
MIGRATION_LOCK = 557314367248862439


def uri() -> str | None:
    value = ENV.POSTGRES_URI.optional()
    if value is None and ENV.LANGSMITH_LANGGRAPH_API_VARIANT.get() == "local_dev":
        value = "postgresql://postgres:postgres@127.0.0.1:5433/postgres"
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


def require_configured() -> None:
    if not configured():
        raise RuntimeError(
            "POSTGRES_URI is required: pull request and repository records are stored in PostgreSQL"
        )


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
        event.listen(_ENGINE.sync_engine, "before_cursor_execute", _query_started)
        event.listen(_ENGINE.sync_engine, "after_cursor_execute", _query_finished)
        event.listen(_ENGINE.sync_engine, "handle_error", _query_failed)
        _ENGINE_URI = database_uri
    return _ENGINE


def _query_started(
    conn: Connection,
    cursor: object,
    statement: str,
    parameters: object,
    context: ExecutionContext,
    executemany: bool,
) -> None:
    conn.info["slow_query_started"] = perf_counter()


def _log_slow_query(conn: Connection, statement: str | None, failed: bool) -> None:
    started = conn.info.pop("slow_query_started", None)
    threshold = ENV.POSTGRES_SLOW_QUERY_MS.get_int(1000)
    if not isinstance(started, float) or threshold <= 0:
        return
    duration_ms = (perf_counter() - started) * 1000
    if duration_ms >= threshold:
        logger.warning(
            "Slow PostgreSQL query",
            extra={
                "duration_ms": round(duration_ms, 2),
                "sql_statement": statement,
                "query_failed": failed,
            },
        )


def _query_finished(
    conn: Connection,
    cursor: object,
    statement: str,
    parameters: object,
    context: ExecutionContext,
    executemany: bool,
) -> None:
    _log_slow_query(conn, statement, False)


def _query_failed(context: ExceptionContext) -> None:
    if context.connection is not None:
        _log_slow_query(context.connection, context.statement, True)


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


@asynccontextmanager
async def read_only_transaction() -> AsyncIterator[AsyncConnection]:
    async with engine().connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL search_path TO {SCHEMA}, public"))
            yield conn
        finally:
            if transaction.is_active:
                await transaction.rollback()
            await conn.invalidate()


@asynccontextmanager
async def snapshot_transaction() -> AsyncIterator[AsyncConnection]:
    """A read-only transaction whose every statement sees one database snapshot.

    ``read_only_transaction`` runs at READ COMMITTED, where each statement takes
    its own snapshot: a multi-statement read can observe rows written after the
    head version it already read, which is exactly the inconsistency a client
    reducing a snapshot plus a live event stream would double-apply.
    """
    async with engine().connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            await conn.execute(text(f"SET LOCAL search_path TO {SCHEMA}, public"))
            yield conn
        finally:
            # Both settings are transaction-scoped, so the rollback is what
            # resets them and the connection goes back to the pool.
            if transaction.is_active:
                await transaction.rollback()


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """An ORM session joined to one ``transaction()``, flushed before it commits."""
    async with transaction() as conn:
        async with AsyncSession(bind=conn, expire_on_commit=False) as orm:
            yield orm
            await orm.flush()


async def migrate() -> None:
    logger.info(
        "Initializing database",
        extra={"database_setting": "POSTGRES_URI", "database_schema": SCHEMA},
    )
    migrations = await asyncio.to_thread(load_migrations)
    async with engine().begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        await conn.run_sync(upgrade, migrations)
    logger.info(
        "Database initialized",
        extra={"database_setting": "POSTGRES_URI", "database_schema": SCHEMA},
    )


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
    revision: str = "heads",
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
    if ENV.OPENSWE_ENV.optional() == "preview":
        drop_superseded_revisions(conn, context, migrations, schema)
    with Operations.context(context):
        context.run_migrations()


def drop_superseded_revisions(
    conn: Connection,
    context: MigrationContext,
    migrations: ScriptDirectory,
    schema: str,
) -> None:
    """Forget stamped revisions that a rebased preview branch now chains beneath another."""
    current = set(context.get_current_heads())
    superseded = current & {
        script.revision
        for head in current
        for script in migrations.walk_revisions(head=head)
        if script.revision != head
    }
    if not superseded:
        return
    logger.warning(
        "Dropping superseded migration revisions",
        extra={"superseded_revisions": sorted(superseded)},
    )
    conn.execute(
        text(f"DELETE FROM {schema}.alembic_version WHERE version_num IN :revisions").bindparams(
            bindparam("revisions", expanding=True)
        ),
        {"revisions": sorted(superseded)},
    )


def execute_revision(conn: Connection, migrations: ScriptDirectory, revision: str) -> None:
    context = MigrationContext.configure(connection=conn)
    with Operations.context(context):
        migrations.get_revision(revision).module.upgrade()


async def close() -> None:
    global _ENGINE, _ENGINE_URI
    if _ENGINE is not None:
        await _ENGINE.dispose()
    _ENGINE = None
    _ENGINE_URI = None
