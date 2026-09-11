"""Deployment initialization and schema regressions."""

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import make_url, text

from agent.analytics import database


async def test_replicas_and_restarts_preserve_identity_without_starting_collection(deployment_db):
    script = (
        "import asyncio\n"
        "from agent.analytics import database\n"
        "async def main():\n"
        "    await database.migrate()\n"
        "    print(database.workspace_id())\n"
        "    await database.close()\n"
        "asyncio.run(main())\n"
    )

    async def replica():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        return UUID(stdout.decode().strip())

    first, second = await asyncio.gather(replica(), replica())
    assert first == second
    await database.migrate()
    assert database.workspace_id() == first
    await database.close()
    await database.migrate()
    assert database.workspace_id() == first
    status = await database.readiness()
    assert status["ready"]
    assert status["collection_started_at"] is None
    assert status["reporting_cutover_at"] is None


@pytest.mark.parametrize("retained_only", [False, True])
async def test_migration_preserves_existing_workspace_and_history(deployment_db, retained_only):
    old_workspace = uuid4()
    run_id = uuid4()
    captured_at = datetime(2026, 9, 1, tzinfo=UTC)
    async with database.engine().begin() as conn:
        for path in sorted(Path(database.__file__).with_name("migrations").glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version >= 7:
                continue
            await database._run_script(conn, path.read_text())
            await conn.execute(
                text(
                    "INSERT INTO schema_migrations (version) VALUES (:version) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"version": version},
            )
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
    await database.migrate()
    assert database.workspace_id() == old_workspace
    async with database.connection() as conn:
        assert await conn.scalar(text("SELECT run_id FROM run_projection")) == run_id
        assert (
            await conn.scalar(text("SELECT reporting_cutover_at FROM deployment_metadata")) is None
        )
        if not retained_only:
            assert await database.collection_started_at(conn) == captured_at
    await database.close()
    await database.migrate()
    assert database.workspace_id() == old_workspace


async def test_standalone_uri_with_sslmode_connects(deployment_db, monkeypatch):
    uri = make_url(os.environ["POSTGRES_URI"]).set(drivername="postgresql")
    uri = uri.update_query_dict({"sslmode": "disable"})
    monkeypatch.setenv("POSTGRES_URI", uri.render_as_string(hide_password=False))
    await database.migrate()
    assert (await database.readiness())["ready"]


@pytest.mark.parametrize(
    "query",
    ["ssl=require&sslmode=require", "sslmode=require&sslmode=disable"],
)
async def test_connection_uri_rejects_ambiguous_ssl_modes(monkeypatch, query):
    monkeypatch.setenv("POSTGRES_URI", f"postgresql://localhost/analytics_test?{query}")
    with pytest.raises(ValueError, match="SSL|sslmode"):
        database.analytics_uri()


async def test_reporting_activation_is_shared_and_preserved_across_restarts(deployment_db):
    await database.migrate()
    before = datetime.now(UTC)
    await asyncio.gather(*(database.activate_reporting() for _ in range(4)))
    async with database.connection() as conn:
        metadata = await database.reporting_metadata(conn)
    cutover = datetime.fromisoformat(metadata["reporting_cutover_at"])
    assert before <= cutover <= datetime.now(UTC)
    assert metadata["collection_started_at"] is None
    assert metadata["completeness"] == "not_started"

    await database.close()
    await database.migrate()
    await database.activate_reporting()
    async with database.connection() as conn:
        restarted = await database.reporting_metadata(conn)
    assert restarted["reporting_cutover_at"] == metadata["reporting_cutover_at"]
    assert restarted["collection_started_at"] is None
