"""Confirmed nonproduction data repairs on private admin surfaces."""

import logging
from typing import Literal

from sqlalchemy import MetaData, Table, and_, delete, insert, update

from openswe.config import ENV
from openswe.database import postgres
from openswe.store import delete_value, put_value
from openswe.tools.access import Policy, access
from openswe.tools.admin_gate import configurable
from openswe.utils.json_types import JsonObject

logger = logging.getLogger(__name__)


def repair_tools_enabled() -> bool:
    return ENV.OPENSWE_ENV.optional() in {"preview", "staging"}


def _require_confirmation(confirm: bool) -> None:
    if not repair_tools_enabled():
        raise ValueError("Data repair tools are only enabled in preview and staging.")
    if not confirm:
        raise ValueError("Review the exact repair with the requester, then pass confirm=true.")


@access(Policy(trusted="admin_surface", actor="admin"))
async def write_store_item(
    namespace: list[str], key: str, value: JsonObject | None, confirm: bool = False
) -> dict[str, object]:
    """Replace one Store item, or delete it with value=null, after explicit confirmation."""
    _require_confirmation(confirm)
    if not namespace or any(not part for part in namespace) or not key:
        raise ValueError("Namespace components and key cannot be empty.")
    logger.warning(
        "Admin Store repair requested",
        extra={
            "github_login": configurable().github_login,
            "operation": "delete" if value is None else "put",
        },
    )
    if value is None:
        await delete_value(namespace, key)
    else:
        await put_value(namespace, key, value)
    return {"ok": True}


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
