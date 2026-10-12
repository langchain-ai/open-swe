from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from openswe.tools.repair_data import write_database_rows, write_store_item


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
    monkeypatch.setattr("openswe.tools.access.langgraph_sdk.get_client", lambda: client)
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
async def test_environment_gate_blocks_both_backends(monkeypatch, environment):
    monkeypatch.setenv("OPENSWE_ENV", environment)
    with patch("openswe.tools.repair_data.put_value", new_callable=AsyncMock) as put:
        for tool, args in [
            (write_store_item, (["users"], "admin", {})),
            (write_database_rows, ("users", "insert", {"id": 1})),
        ]:
            with pytest.raises(ValueError, match="only enabled"):
                await tool(*args, confirm=True)
        put.assert_not_awaited()


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
        monkeypatch.setattr("openswe.tools.access.langgraph_sdk.get_client", lambda: client)
    with patch("openswe.tools.repair_data.put_value", new_callable=AsyncMock) as put:
        result = await write_store_item(["users"], "admin", {}, confirm=True)
        assert result["ok"] is False
        put.assert_not_awaited()
    result = await write_database_rows("users", "insert", {"id": 1}, confirm=True)
    assert result["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["preview", "staging"])
async def test_confirmed_store_put_and_delete(monkeypatch, environment):
    monkeypatch.setenv("OPENSWE_ENV", environment)
    with (
        patch("openswe.tools.repair_data.put_value", new_callable=AsyncMock) as put,
        patch("openswe.tools.repair_data.delete_value", new_callable=AsyncMock) as delete,
    ):
        with pytest.raises(ValueError, match="confirm=true"):
            await write_store_item(["users"], "admin", {})
        put.assert_not_awaited()
        await write_store_item(["users"], "admin", {"enabled": True}, confirm=True)
        put.assert_awaited_once_with(["users"], "admin", {"enabled": True})
        await write_store_item(["users"], "admin", None, confirm=True)
        delete.assert_awaited_once_with(["users"], "admin")


@pytest.mark.asyncio
async def test_database_repair_requires_filters_and_rolls_back_large_update(monkeypatch):
    with pytest.raises(ValueError, match="exact-match filters"):
        await write_database_rows("users", "delete", confirm=True)
    with pytest.raises(ValueError, match="confirm=true"):
        await write_database_rows("users", "insert", {"id": 1})
    target = Table("users", MetaData(), Column("id", Integer), Column("value", Integer))
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
        await write_database_rows("users", "update", {"value": 2}, {"id": 1}, confirm=True)
    assert not committed
    conn.execute.return_value = SimpleNamespace(rowcount=1)
    result = await write_database_rows("users", "update", {"value": 2}, {"id": 1}, confirm=True)
    assert result == {"ok": True, "row_count": 1}
    assert committed == [True]
    statement = conn.execute.await_args.args[0]
    assert statement.compile().params == {"value": 2, "id_1": 1}
