"""Confirmed nonproduction data repairs on private admin surfaces."""

import logging
from typing import Literal

from sqlalchemy import MetaData, Table, and_, delete, insert, update

from openswe.config import ENV
from openswe.database import postgres
from openswe.tools.access import Policy, access
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


def repair_tools_enabled() -> bool:
    return ENV.OPENSWE_ENV.optional() in {"preview", "staging"}


def _require_confirmation(confirm: bool) -> None:
    if not repair_tools_enabled():
        raise ValueError("Data repair tools are only enabled in preview and staging.")
    if not confirm:
        raise ValueError("Review the exact repair with the requester, then pass confirm=true.")


@expose_mcp(access="admin")
@access(Policy(trusted="admin_surface", actor="admin"))
async def execute_database_sql(query: str, confirm: bool = False) -> dict[str, object]:
    """Execute an admin-confirmed SQL statement, including DDL and migration repairs."""
    _require_confirmation(confirm)
    if not query.strip():
        raise ValueError("Query cannot be empty.")
    if len(query) > 200_000:
        raise ValueError("Query exceeds the 200,000-character limit.")
    logger.warning(
        "Admin SQL repair requested",
        extra={"github_login": configurable().github_login},
    )
    async with postgres.engine().connect() as conn:
        try:
            async with conn.begin():
                await conn.exec_driver_sql(f"SET LOCAL search_path TO {postgres.SCHEMA}, public")
                await conn.exec_driver_sql("SET LOCAL statement_timeout = 60000")
                await conn.exec_driver_sql("SET LOCAL lock_timeout = 3000")
                result = await conn.exec_driver_sql(query)
                row_count = result.rowcount
                result.close()
        finally:
            await conn.invalidate()
    return {"ok": True, "row_count": row_count}


@expose_mcp(access="admin")
@access(Policy(trusted="admin_surface", actor="admin"))
async def write_database_rows(
    table: str,
    operation: Literal["insert", "update", "delete"],
    values: JsonObject | None = None,
    where: JsonObject | None = None,
    confirm: bool = False,
) -> dict[str, object]:
    """Repair Open SWE rows, including alembic_version; update/delete need exact-match filters."""
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
