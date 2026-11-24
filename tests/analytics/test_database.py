"""Deployment initialization and schema regressions."""

import asyncio
import socket
import struct
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from alembic.util import CommandError
from blockbuster import BlockBuster
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from openswe.database import analytics as database
from openswe.database import postgres
from tests.analytics.conftest import initialize_database


async def test_migrations_allow_nonblocking_startup_and_restart(deployment_db):
    detector = BlockBuster()
    identities = []
    migrations = postgres.load_migrations()
    async with postgres.engine().begin() as conn:
        await conn.execute(text("CREATE SCHEMA open_swe"))
        await conn.run_sync(postgres.upgrade, migrations, "open_swe", "3047efb4bf0d")
        await conn.execute(text("CREATE INDEX thread_index_activity ON thread_index (updated_at)"))
        await conn.execute(
            text(
                "INSERT INTO thread_index (id, thread_id, runtime, metadata, updated_at) VALUES "
                "(:one, 'activity', 'mda', '{\"updated_at_ms\": 1788220800000}', '2026-10-01'), "
                "(:two, 'fallback', 'mda', '{}', '2026-09-01')"
            ),
            {"one": uuid4(), "two": uuid4()},
        )
        await conn.execute(
            text(
                "INSERT INTO thread_index (id, thread_id, runtime, updated_at) "
                "SELECT gen_random_uuid(), 'legacy-' || n, 'mda', '2026-09-01'::timestamptz "
                "FROM generate_series(1, 2001) AS n"
            )
        )
    for _ in range(2):
        # asyncpg checks local password/SSL files when opening a new connection.
        async with postgres.engine().connect():
            pass
        await asyncio.wait_for(asyncio.gather(postgres.migrate(), postgres.migrate()), 30)
        detector.activate()
        try:
            await initialize_database()
            identities.append(database.workspace_id())
            assert (await database.readiness())["ready"]
        finally:
            detector.deactivate()
        await postgres.close()
    assert identities[0] == identities[1]
    async with postgres.connection() as conn:
        assert await conn.scalar(text("SELECT to_regclass('thread_index_activity')")) is None
        assert await conn.scalar(
            text(
                "SELECT indisvalid FROM pg_index WHERE indexrelid = 'thread_index_last_activity'::regclass"
            )
        )
        assert (
            list(
                await conn.scalars(
                    text("SELECT last_activity_at FROM thread_index ORDER BY thread_id")
                )
            )
            == [datetime(2026, 9, 1, tzinfo=UTC)] * 2003
        )
        assert not await conn.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid())"
            )
        )


@pytest.mark.parametrize("retained_only", [False, True])
async def test_migration_preserves_existing_workspace_and_history(deployment_db, retained_only):
    old_workspace = uuid4()
    run_id = uuid4()
    captured_at = datetime(2026, 9, 1, tzinfo=UTC)
    migrations = postgres.load_migrations()
    async with postgres.engine().begin() as conn:
        await conn.execute(text("CREATE SCHEMA open_swe"))
        await conn.run_sync(postgres.upgrade, migrations, "open_swe", "0006")
        await conn.execute(
            text("INSERT INTO run_projection (workspace_id, run_id) VALUES (:workspace, :run)"),
            {"workspace": old_workspace, "run": run_id},
        )
        if not retained_only:
            await conn.execute(
                text(
                    "INSERT INTO outbox (event_id, workspace_id, event_body, created_at) "
                    "VALUES (:event, :workspace, '{}', :captured_at)"
                ),
                {"event": uuid4(), "workspace": old_workspace, "captured_at": captured_at},
            )
    await initialize_database()
    assert database.workspace_id() == old_workspace
    async with postgres.connection() as conn:
        assert await conn.scalar(text("SELECT run_id FROM run_projection")) == run_id
        assert (
            await conn.scalar(text("SELECT reporting_cutover_at FROM deployment_metadata")) is None
        )
        if not retained_only:
            assert await database.collection_started_at(conn) == captured_at
    await postgres.close()
    await initialize_database()
    assert database.workspace_id() == old_workspace


async def test_preview_forgets_revision_whose_script_left_the_tree(deployment_db, monkeypatch):
    migrations = postgres.load_migrations()
    heads = migrations.get_heads()
    async with postgres.engine().connect() as conn:
        await conn.execute(text("CREATE SCHEMA open_swe"))
        await conn.commit()
        await conn.run_sync(
            postgres.upgrade,
            migrations,
            "open_swe",
            migrations.get_revision(heads[0]).down_revision,
        )
        await conn.execute(text("INSERT INTO open_swe.alembic_version VALUES ('a8caff4f6051')"))
        await conn.commit()
    with pytest.raises(CommandError, match="a8caff4f6051"):
        await postgres.migrate()
    monkeypatch.setenv("OPENSWE_ENV", "preview")
    await postgres.migrate()
    async with postgres.connection() as conn:
        versions = await conn.scalars(text("SELECT version_num FROM open_swe.alembic_version"))
        assert set(versions) == set(heads)


async def test_pool_replaces_a_connection_the_server_closed(deployment_db, monkeypatch):
    monkeypatch.setenv("POSTGRES_PING_AFTER_IDLE_SECONDS", "0")
    async with postgres.connection() as conn:
        closed = await conn.scalar(text("SELECT pg_backend_pid()"))
    other = create_async_engine(postgres.uri(), poolclass=NullPool)
    try:
        async with other.connect() as conn:
            assert await conn.scalar(
                text("SELECT pg_terminate_backend(:pid, 5000)"), {"pid": closed}
            )
    finally:
        await other.dispose()

    async with postgres.transaction() as conn:
        assert await conn.scalar(text("SELECT pg_backend_pid()")) != closed


class _TracedConnection:
    """A driver connection wrapped as Datadog's tracer wraps it: no asyncpg.Connection."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self.__wrapped__ = connection

    def __getattr__(self, name: str) -> object:
        return getattr(self.__wrapped__, name)


@pytest.mark.parametrize("traced", [False, True])
async def test_pool_replaces_connections_a_failover_dropped_silently(
    deployment_db, monkeypatch, traced
):
    monkeypatch.setenv("POSTGRES_PING_AFTER_IDLE_SECONDS", "0")
    if traced:
        connect = asyncpg.connect

        async def traced_connect(*args: object, **kwargs: object) -> _TracedConnection:
            return _TracedConnection(await connect(*args, **kwargs))

        monkeypatch.setattr(asyncpg, "connect", traced_connect)
    database = make_url(postgres.uri())
    relays: list[tuple[asyncio.StreamWriter, asyncio.Event]] = []

    async def relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
        server_reader, server_writer = await asyncio.open_connection(database.host, database.port)
        dropped = asyncio.Event()
        relays.append((server_writer, dropped))

        async def to_server() -> None:
            while data := await client_reader.read(65536):
                if dropped.is_set():
                    # Linger 0: close with a reset, as a host that never had the connection does.
                    client_writer.get_extra_info("socket").setsockopt(
                        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                    )
                    client_writer.transport.abort()
                    return
                server_writer.write(data)

        async def to_client() -> None:
            while data := await server_reader.read(65536):
                client_writer.write(data)
            if not dropped.is_set():
                client_writer.close()

        await asyncio.gather(to_server(), to_client(), return_exceptions=True)

    proxy = await asyncio.start_server(relay, "127.0.0.1", 0)
    port = proxy.sockets[0].getsockname()[1]
    monkeypatch.setenv(
        "POSTGRES_URI",
        database.set(host="127.0.0.1", port=port).render_as_string(hide_password=False),
    )

    async def run(statement: str) -> None:
        async with postgres.transaction() as conn:
            await conn.execute(text(statement))

    try:
        await asyncio.gather(*(run("SELECT pg_sleep(0.1)") for _ in range(4)))
        for server_writer, dropped in relays:
            dropped.set()
            server_writer.transport.abort()
        await asyncio.gather(*(run("SELECT 1") for _ in range(4)))
    finally:
        await postgres.close()
        proxy.close()


class _CountedPings:
    """Counts the checkout pings sent through each driver connection the pool opens."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.pings = 0
        connect = asyncpg.connect
        counter = self

        class Counted(_TracedConnection):
            async def execute(self, query: str, *args: object, **kwargs: object) -> str:
                if query == "SELECT 1":
                    counter.pings += 1
                return await self.__wrapped__.execute(query, *args, **kwargs)

        async def counted_connect(*args: object, **kwargs: object) -> Counted:
            return Counted(await connect(*args, **kwargs))

        monkeypatch.setattr(asyncpg, "connect", counted_connect)


@pytest.mark.parametrize(("idle_seconds", "pings"), [("3600", 0), ("0", 2)])
async def test_pool_pings_only_connections_idle_past_the_limit(
    deployment_db, monkeypatch, idle_seconds, pings
):
    monkeypatch.setenv("POSTGRES_PING_AFTER_IDLE_SECONDS", idle_seconds)
    await postgres.close()
    counted = _CountedPings(monkeypatch)
    try:
        async with postgres.transaction() as conn:
            await conn.execute(text("SELECT 2"))
        # A fresh connection was never returned, so its first checkout pings either way.
        counted.pings = 0
        for _ in range(2):
            async with postgres.transaction() as conn:
                await conn.execute(text("SELECT 2"))
        assert counted.pings == pings
    finally:
        await postgres.close()


async def test_pool_recovers_when_an_unpinged_connection_was_dropped(deployment_db, monkeypatch):
    monkeypatch.setenv("POSTGRES_PING_AFTER_IDLE_SECONDS", "3600")
    async with postgres.connection() as conn:
        closed = await conn.scalar(text("SELECT pg_backend_pid()"))
    other = create_async_engine(postgres.uri(), poolclass=NullPool)
    try:
        async with other.connect() as conn:
            assert await conn.scalar(
                text("SELECT pg_terminate_backend(:pid, 5000)"), {"pid": closed}
            )
    finally:
        await other.dispose()

    failures = 0
    for _ in range(3):
        try:
            async with postgres.transaction() as conn:
                assert await conn.scalar(text("SELECT pg_backend_pid()")) != closed
        except Exception:  # noqa: BLE001 - the one query a dead connection may fail
            failures += 1
    assert failures <= 1
    async with postgres.transaction() as conn:
        assert await conn.scalar(text("SELECT pg_backend_pid()")) != closed


@pytest.mark.parametrize(
    "query",
    ["ssl=require&sslmode=require", "sslmode=require&sslmode=disable"],
)
async def test_connection_uri_rejects_ambiguous_ssl_modes(monkeypatch, query):
    monkeypatch.setenv("POSTGRES_URI", f"postgresql://localhost/analytics_test?{query}")
    with pytest.raises(ValueError, match="SSL|sslmode"):
        postgres.uri()
