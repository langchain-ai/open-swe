"""Deployment initialization and schema regressions."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from blockbuster import BlockBuster
from sqlalchemy import text

from agent.database import analytics as database
from agent.database import postgres
from tests.analytics.conftest import initialize_database


async def test_migrations_allow_nonblocking_startup_and_restart(deployment_db):
    detector = BlockBuster()
    identities = []
    for _ in range(2):
        # asyncpg checks local password/SSL files when opening a new connection.
        async with postgres.engine().connect():
            pass
        detector.activate()
        try:
            await initialize_database()
            identities.append(database.workspace_id())
            assert (await database.readiness())["ready"]
        finally:
            detector.deactivate()
        await postgres.close()
    assert identities[0] == identities[1]


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


async def test_preview_recovers_screenshot_migration_head(
    deployment_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENSWE_ENV", "preview")
    workspace_id = uuid4()
    migrations = postgres.load_migrations()
    async with postgres.engine().begin() as conn:
        await conn.execute(text("CREATE SCHEMA open_swe"))
        for revision in ("1a27b64154a3", "52fab62a7608"):
            await conn.run_sync(postgres.upgrade, migrations, "open_swe", revision)
        await conn.execute(
            text(
                "ALTER TABLE human_review_request ADD COLUMN screenshot_body text NOT NULL DEFAULT ''"
            )
        )
        await conn.execute(text("DELETE FROM open_swe.alembic_version"))
        await conn.execute(text("INSERT INTO open_swe.alembic_version VALUES ('3cce7935a681')"))
        await conn.execute(
            text("INSERT INTO workspace (id, slug, name) VALUES (:id, 'preview', 'Preview')"),
            {"id": workspace_id},
        )
    for _ in range(2):
        await postgres.migrate()
    async with postgres.connection() as conn:
        assert (
            await conn.scalar(text("SELECT id FROM workspace WHERE slug = 'preview'"))
            == workspace_id
        )
        assert set(await conn.scalars(text("SELECT version_num FROM alembic_version"))) == set(
            migrations.get_heads()
        )


@pytest.mark.parametrize(
    "query",
    ["ssl=require&sslmode=require", "sslmode=require&sslmode=disable"],
)
async def test_connection_uri_rejects_ambiguous_ssl_modes(monkeypatch, query):
    monkeypatch.setenv("POSTGRES_URI", f"postgresql://localhost/analytics_test?{query}")
    with pytest.raises(ValueError, match="SSL|sslmode"):
        postgres.uri()
