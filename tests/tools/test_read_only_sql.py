from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.tools import read_only_sql as query_tool

sql_tool = import_module("agent.tools.read_only_sql")


def _config(**configurable: object) -> dict[str, dict[str, object]]:
    return {"configurable": configurable}


@pytest.mark.asyncio
async def test_read_only_sql_requires_private_admin_dashboard_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    configs = (
        _config(source="dashboard", github_login="admin"),
        _config(admin_thread=True, source="slack", github_login="admin"),
        _config(admin_thread=True, source="schedule", github_login="admin"),
    )

    for config in configs:
        with patch("agent.run_config.get_config", return_value=config):
            result = await query_tool("SELECT 1")
        assert result == {
            "ok": False,
            "error": "Read-only SQL is available only in an admin's private dashboard thread.",
        }


@pytest.mark.asyncio
async def test_read_only_sql_rechecks_admin_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    with patch(
        "agent.run_config.get_config",
        return_value=_config(admin_thread=True, source="dashboard", github_login="not-admin"),
    ):
        result = await query_tool("SELECT 1")

    assert result == {"ok": False, "error": "Only workspace admins can query the database."}


@pytest.mark.asyncio
async def test_read_only_sql_returns_json_safe_limited_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    result = MagicMock()
    result.keys.return_value = ["created_at", "cost"]
    result.fetchmany = AsyncMock(
        return_value=[(datetime(2026, 9, 15, tzinfo=UTC), Decimal("1.25"))]
    )
    conn = AsyncMock()
    conn.stream.return_value = result

    @asynccontextmanager
    async def connection():
        yield conn

    monkeypatch.setattr(sql_tool.postgres, "read_only_transaction", connection)
    with patch(
        "agent.run_config.get_config",
        return_value=_config(admin_thread=True, source="dashboard", github_login="admin"),
    ):
        response = await query_tool("SELECT created_at, cost FROM usage")

    assert response == {
        "ok": True,
        "columns": ["created_at", "cost"],
        "rows": [["2026-09-15T00:00:00+00:00", "1.25"]],
        "row_count": 1,
        "truncated": False,
    }
    conn.execute.assert_awaited_once()
    assert str(conn.execute.await_args.args[0]) == "SET LOCAL statement_timeout = 60000"
    conn.stream.assert_awaited_once()
    assert str(conn.stream.await_args.args[0]) == "SELECT created_at, cost FROM usage"


@pytest.mark.asyncio
async def test_read_only_sql_hides_database_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")

    @asynccontextmanager
    async def connection():
        raise RuntimeError("secret database detail")
        yield

    monkeypatch.setattr(sql_tool.postgres, "read_only_transaction", connection)
    with patch(
        "agent.run_config.get_config",
        return_value=_config(admin_thread=True, source="dashboard", github_login="admin"),
    ):
        response = await query_tool("DELETE FROM users")

    assert response == {"ok": False, "error": "The database rejected the read-only query."}
