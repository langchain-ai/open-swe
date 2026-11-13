"""Deployment initialization and schema regressions."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic.util import CommandError
from blockbuster import BlockBuster
from sqlalchemy import text

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
    async with postgres.engine().begin() as conn:
        await conn.execute(text("CREATE SCHEMA open_swe"))
        await conn.run_sync(
            postgres.upgrade,
            migrations,
            "open_swe",
            migrations.get_revision(heads[0]).down_revision,
        )
        await conn.execute(text("INSERT INTO alembic_version VALUES ('a8caff4f6051')"))
    with pytest.raises(CommandError, match="a8caff4f6051"):
        await postgres.migrate()
    monkeypatch.setenv("OPENSWE_ENV", "preview")
    await postgres.migrate()
    async with postgres.connection() as conn:
        versions = await conn.scalars(text("SELECT version_num FROM open_swe.alembic_version"))
        assert set(versions) == set(heads)


@pytest.mark.parametrize(
    "query",
    ["ssl=require&sslmode=require", "sslmode=require&sslmode=disable"],
)
async def test_connection_uri_rejects_ambiguous_ssl_modes(monkeypatch, query):
    monkeypatch.setenv("POSTGRES_URI", f"postgresql://localhost/analytics_test?{query}")
    with pytest.raises(ValueError, match="SSL|sslmode"):
        postgres.uri()
