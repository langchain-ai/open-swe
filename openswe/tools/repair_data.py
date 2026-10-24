"""Confirmed nonproduction data repairs on private admin surfaces."""

import logging
from typing import Literal

from sqlalchemy import MetaData, Table, and_, delete, insert, text, update

from openswe.config import ENV
from openswe.database import postgres
from openswe.tools.access import Policy, access
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp
from openswe.tools.read_only_sql import MAX_QUERY_CHARS, MAX_ROWS, rows_response
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

_SQL_STATEMENT_TIMEOUT_MS = 60_000
_SQL_LOCK_TIMEOUT_MS = 10_000


def repair_tools_enabled() -> bool:
    return ENV.OPENSWE_ENV.optional() in {"preview", "staging"}


def _require_confirmation(confirm: bool) -> None:
    if not repair_tools_enabled():
        raise ValueError("Data repair tools are only enabled in preview and staging.")
    if not confirm:
        raise ValueError("Review the exact repair with the requester, then pass confirm=true.")


@expose_mcp(access="admin")
@access(Policy(trusted="admin_surface", actor="admin"))
async def write_database_rows(
    table: str,
    operation: Literal["insert", "update", "delete"],
    values: JsonObject | None = None,
    where: JsonObject | None = None,
    confirm: bool = False,
) -> dict[str, object]:
    """Repair rows in an Open SWE table; update/delete require exact-match filters."""
    _require_confirmation(confirm)
    if operation not in {"insert", "update", "delete"}:
        raise ValueError("Operation must be insert, update, or delete.")
    if operation != "insert" and not where:
        raise ValueError("Update and delete require nonempty exact-match filters.")
    if operation == "insert" and where:
        raise ValueError("Insert does not accept filters.")
    if operation != "delete" and not values:
        raise ValueError("Insert and update require values.")
    if operation == "delete" and values:
        raise ValueError("Delete does not accept values.")
    logger.warning(
        "Admin database repair requested",
        extra={
            "github_login": configurable().github_login,
            "operation": operation,
            "table_name": table,
        },
    )
    async with postgres.transaction() as conn:
        await conn.exec_driver_sql("SET LOCAL statement_timeout = 10000")
        await conn.exec_driver_sql("SET LOCAL lock_timeout = 3000")
        target = await conn.run_sync(
            lambda sync: Table(table, MetaData(), schema=postgres.SCHEMA, autoload_with=sync)
        )
        if table == "alembic_version":
            raise ValueError("Migration metadata cannot be repaired with this tool.")
        unknown = (set(values or {}) | set(where or {})) - set(target.c.keys())
        if unknown:
            raise ValueError("Unknown column names.")
        if operation == "insert":
            statement = insert(target).values(**(values or {}))
        else:
            predicate = and_(*(target.c[name] == value for name, value in (where or {}).items()))
            statement = (
                update(target).where(predicate).values(**(values or {}))
                if operation == "update"
                else delete(target).where(predicate)
            )
        result = await conn.execute(statement)
        if result.rowcount > 100:
            raise ValueError(
                "Repair exceeds 100 rows; transaction rolled back. Narrow the filters."
            )
    return {"ok": True, "row_count": result.rowcount}


@expose_mcp(access="admin")
@access(Policy(trusted="admin_surface", actor="admin"))
async def run_sql(query: str, confirm: bool = False) -> dict[str, object]:
    """Run one SQL statement, writes included, against a preview or staging database."""
    _require_confirmation(confirm)
    query = query.strip()
    if not query:
        return {"ok": False, "error": "Query cannot be empty."}
    if len(query) > MAX_QUERY_CHARS:
        return {"ok": False, "error": f"Query exceeds the {MAX_QUERY_CHARS:,}-character limit."}
    logger.warning(
        "Admin SQL statement requested",
        extra={"github_login": configurable().github_login, "query_chars": len(query)},
    )
    try:
        async with postgres.transaction() as conn:
            await conn.exec_driver_sql(f"SET LOCAL statement_timeout = {_SQL_STATEMENT_TIMEOUT_MS}")
            await conn.exec_driver_sql(f"SET LOCAL lock_timeout = {_SQL_LOCK_TIMEOUT_MS}")
            result = await conn.execute(text(query))
            if not result.returns_rows:
                return {"ok": True, "row_count": result.rowcount}
            columns = [str(column) for column in result.keys()]
            fetched = result.fetchmany(MAX_ROWS + 1)
    except Exception as exc:
        logger.warning("Admin SQL statement failed", extra={"error_type": type(exc).__name__})
        # The admin wrote the statement, so the database's own message is what they need.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return rows_response(columns, fetched)
