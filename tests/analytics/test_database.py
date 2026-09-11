"""Deployment initialization regressions using disposable PostgreSQL databases."""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from agent.analytics import database, emitter, ingestion, outbox, queries
from agent.analytics.events import EventName, RunStartedPayload, make_event


@pytest.fixture
async def deployment_db(monkeypatch):
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.skip("TEST_ANALYTICS_POSTGRES_URI is required for PostgreSQL regressions")
    admin = create_async_engine(uri, isolation_level="AUTOCOMMIT")
    db_name = f"analytics_deployment_test_{uuid4().hex}"
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    test_uri = make_url(uri).set(database=db_name).render_as_string(hide_password=False)
    await database.close()
    monkeypatch.setenv("POSTGRES_URI", test_uri)
    try:
        yield
    finally:
        await database.close()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
        await admin.dispose()


def run_event(*, occurred_at=None, person=None):
    return make_event(
        workspace_id=database.workspace_id(),
        event_name=EventName.RUN_STARTED,
        producer="test",
        producer_event_id=str(uuid4()),
        occurred_at=occurred_at or datetime.now(UTC),
        environment="test",
        payload=RunStartedPayload(model_attribution_quality="unavailable"),
        run_id=uuid4(),
        user_id=person,
    )


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
    person = emitter.opaque_person("github", 123)
    await database.close()
    await database.migrate()
    assert database.workspace_id() == first
    assert emitter.opaque_person("github", 123) == person
    status = await database.readiness()
    assert status["ready"]
    assert status["collection_started_at"] is None
    report = await queries.usage_leaderboard(period="all", limit=10, current_login=None, admin=True)
    assert report["rows"] == []
    assert report["analytics_epoch"] is None
    assert report["completeness"] == "not_started"


async def test_capture_start_survives_delivery_retention_and_restart(deployment_db):
    await database.migrate()
    person = emitter.opaque_person("github", 123)
    events = [run_event(person=person), run_event(person=person)]
    before = datetime.now(UTC)
    assert await asyncio.gather(*(outbox.enqueue(event) for event in events)) == [True, True]
    async with database.connection() as conn:
        started_at = await database.collection_started_at(conn)
    assert before <= started_at <= datetime.now(UTC)
    assert await outbox.deliver_batch() == 2
    assert not await outbox.enqueue(events[0])

    delayed = run_event(occurred_at=before - timedelta(days=60), person=person)
    assert await ingestion.ingest(delayed)
    assert not await ingestion.ingest(delayed)
    report = await queries.usage_leaderboard(period="all", limit=10, current_login=None, admin=True)
    assert report["rows"][0]["agent_runs"] == 3
    assert report["analytics_epoch"] == started_at.isoformat()
    assert report["completeness"] == "observed_events_only"
    async with database.transaction() as conn:
        await conn.execute(text("DELETE FROM events"))
        await conn.execute(text("DELETE FROM outbox"))
        await conn.execute(text("DELETE FROM ingestion_receipts"))
    await database.close()
    await database.migrate()
    assert emitter.opaque_person("github", 123) == person
    async with database.connection() as conn:
        assert await database.collection_started_at(conn) == started_at
        assert await conn.scalar(text("SELECT count(*) FROM run_projection")) == 3


async def test_failed_ingestion_does_not_start_collection(deployment_db):
    await database.migrate()
    async with database.transaction() as conn:
        await conn.execute(
            text("ALTER TABLE additive_event_projection ADD CONSTRAINT reject_event CHECK (false)")
        )
    event = run_event()
    with pytest.raises(IntegrityError):
        await ingestion.ingest(event)
    async with database.transaction() as conn:
        assert await database.collection_started_at(conn) is None
        assert await conn.scalar(text("SELECT count(*) FROM events")) == 0
        await conn.execute(
            text("ALTER TABLE additive_event_projection DROP CONSTRAINT reject_event")
        )
    assert await ingestion.ingest(event)
    async with database.connection() as conn:
        assert await database.collection_started_at(conn) is not None


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
        if not retained_only:
            assert await database.collection_started_at(conn) == captured_at
    await database.close()
    await database.migrate()
    assert database.workspace_id() == old_workspace
