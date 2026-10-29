from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import Column, MetaData, String, Table, text
from sqlalchemy.exc import DBAPIError

from openswe.database import postgres
from openswe.tools.repair_data import execute_database_sql, write_database_rows


@pytest.fixture(autouse=True)
def admin_context(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    monkeypatch.setenv("OPENSWE_ENV", "preview")
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"metadata": {"visibility": "private", "owner_type": "user"}}
            )
        )
    )
    monkeypatch.setattr("openswe.utils.thread_reads.langgraph_sdk.get_client", lambda: client)
    config = {
        "configurable": {
            "thread_id": "t-1",
            "source": "dashboard",
            "admin_thread": True,
            "github_login": "admin",
        }
    }
    with patch("openswe.run_config.get_config", return_value=config):
        yield config


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["production", "prod", "development", "", "unknown"])
async def test_environment_gate_blocks_repairs(monkeypatch, environment):
    monkeypatch.setenv("OPENSWE_ENV", environment)
    with pytest.raises(ValueError, match="only enabled"):
        await write_database_rows("users", "insert", {"id": 1}, confirm=True)
    with pytest.raises(ValueError, match="only enabled"):
        await execute_database_sql("DELETE FROM alembic_version", confirm=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["nonadmin", "public"])
async def test_access_gate_blocks_writes(admin_context, monkeypatch, change):
    if change == "nonadmin":
        admin_context["configurable"]["github_login"] = "other"
    else:
        client = SimpleNamespace(
            threads=SimpleNamespace(
                get=AsyncMock(
                    return_value={"metadata": {"visibility": "public", "owner_type": "user"}}
                )
            )
        )
        monkeypatch.setattr("openswe.utils.thread_reads.langgraph_sdk.get_client", lambda: client)
    result = await write_database_rows("users", "insert", {"id": 1}, confirm=True)
    assert result["ok"] is False
    result = await execute_database_sql("DELETE FROM alembic_version", confirm=True)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_sql_repair_requires_confirmation_before_connecting(monkeypatch):
    engine = AsyncMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(postgres, "engine", engine)
    with pytest.raises(ValueError, match="confirm=true"):
        await execute_database_sql("DELETE FROM alembic_version")
    with pytest.raises(ValueError, match="empty"):
        await execute_database_sql("  ", confirm=True)
    engine.assert_not_called()


@pytest.mark.asyncio
async def test_sql_repair_commits_ddl_bulk_writes_and_migration_metadata(registry_db):
    await execute_database_sql("CREATE TABLE repair_example (id integer)", confirm=True)
    result = await execute_database_sql(
        "INSERT INTO repair_example SELECT generate_series(1, 101)", confirm=True
    )
    assert result == {"ok": True, "row_count": 101}
    result = await execute_database_sql(
        "UPDATE alembic_version SET version_num = 'repaired'", confirm=True
    )
    assert result["row_count"] == 1
    await execute_database_sql("ALTER TABLE repair_example ADD COLUMN config jsonb", confirm=True)
    await execute_database_sql(
        'UPDATE repair_example SET config = \'{"enabled":true,"disabled":false,"empty":null,"n":1}\'::jsonb',
        confirm=True,
    )
    async with postgres.connection() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM repair_example")) == 101
        assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == "repaired"
        assert await conn.scalar(text("SELECT config FROM repair_example LIMIT 1")) == {
            "enabled": True,
            "disabled": False,
            "empty": None,
            "n": 1,
        }
    with pytest.raises(DBAPIError):
        await execute_database_sql(
            "WITH changed AS (DELETE FROM repair_example RETURNING id) "
            "INSERT INTO repair_example SELECT id / (id - id) FROM changed",
            confirm=True,
        )
    async with postgres.connection() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM repair_example")) == 101
    await execute_database_sql("SELECT pg_advisory_lock(815196730)", confirm=True)
    async with postgres.connection() as conn:
        assert await conn.scalar(text("SELECT pg_try_advisory_lock(815196730)")) is True
        await conn.execute(text("SELECT pg_advisory_unlock(815196730)"))


@pytest.mark.asyncio
async def test_database_repair_requires_filters_and_rolls_back_large_update(monkeypatch):
    with pytest.raises(ValueError, match="exact-match filters"):
        await write_database_rows("users", "delete", confirm=True)
    with pytest.raises(ValueError, match="confirm=true"):
        await write_database_rows("users", "insert", {"id": 1})
    target = Table("alembic_version", MetaData(), Column("version_num", String(32)))
    values = {"version_num": "new_revision"}
    where = {"version_num": "old_revision"}
    conn = AsyncMock()
    conn.run_sync.return_value = target
    conn.execute.return_value = SimpleNamespace(rowcount=101)
    committed = []

    @asynccontextmanager
    async def transaction():
        yield conn
        committed.append(True)

    monkeypatch.setattr("openswe.tools.repair_data.postgres.transaction", transaction)
    with pytest.raises(ValueError, match="rolled back"):
        await write_database_rows("alembic_version", "update", values, where, confirm=True)
    assert not committed
    conn.execute.return_value = SimpleNamespace(rowcount=1)
    result = await write_database_rows("alembic_version", "update", values, where, confirm=True)
    assert result == {"ok": True, "row_count": 1}
    assert committed == [True]
    statement = conn.execute.await_args.args[0]
    assert statement.compile().params == {
        "version_num": "new_revision",
        "version_num_1": "old_revision",
    }
