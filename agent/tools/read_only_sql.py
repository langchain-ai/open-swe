"""Read-only PostgreSQL access for private admin surfaces."""

import json
import logging
import math
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

from agent.database import postgres
from agent.tools.admin_gate import require_private_admin_surface

logger = logging.getLogger(__name__)

_MAX_QUERY_CHARS = 200_000
_MAX_ROWS = 1_000
_MAX_OUTPUT_BYTES = 1_000_000
_STATEMENT_TIMEOUT_MS = 60_000


def _json_value(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


async def read_only_sql(query: str) -> dict[str, object]:
    """Run one read-only PostgreSQL query on a private admin surface."""
    if error := await require_private_admin_surface("query the database"):
        return {"ok": False, "error": error}

    query = query.strip()
    if not query:
        return {"ok": False, "error": "Query cannot be empty."}
    if len(query) > _MAX_QUERY_CHARS:
        return {
            "ok": False,
            "error": f"Query exceeds the {_MAX_QUERY_CHARS:,}-character limit.",
        }

    try:
        async with postgres.read_only_transaction() as conn:
            await conn.execute(text(f"SET LOCAL statement_timeout = {_STATEMENT_TIMEOUT_MS}"))
            result = await conn.stream(text(query))
            columns = [str(column) for column in result.keys()]
            fetched = await result.fetchmany(_MAX_ROWS + 1)
    except Exception as exc:
        logger.warning(
            "Read-only SQL query failed",
            extra={"error_type": type(exc).__name__},
        )
        return {"ok": False, "error": "The database rejected the read-only query."}

    rows: list[list[object]] = []
    truncated = len(fetched) > _MAX_ROWS
    response: dict[str, object] = {
        "ok": True,
        "columns": columns,
        "rows": rows,
        "row_count": 0,
        "truncated": truncated,
    }
    for record in fetched[:_MAX_ROWS]:
        row = [_json_value(value) for value in record]
        rows.append(row)
        response["row_count"] = len(rows)
        if len(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()) > (
            _MAX_OUTPUT_BYTES
        ):
            rows.pop()
            response["row_count"] = len(rows)
            response["truncated"] = True
            break

    return response
