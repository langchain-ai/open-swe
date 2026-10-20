"""Read-only PostgreSQL access for private admin surfaces."""

import json
import logging
import math
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

from openswe.database import postgres
from openswe.tools.access import Policy, access
from openswe.tools.mcp_exposure import expose_mcp

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 200_000
MAX_ROWS = 1_000
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


@expose_mcp(access="admin")
@access(Policy(trusted="admin_surface", actor="admin"))
async def read_only_sql(query: str) -> dict[str, object]:
    """Run one read-only PostgreSQL query on a private admin surface."""
    query = query.strip()
    if not query:
        return {"ok": False, "error": "Query cannot be empty."}
    if len(query) > MAX_QUERY_CHARS:
        return {
            "ok": False,
            "error": f"Query exceeds the {MAX_QUERY_CHARS:,}-character limit.",
        }

    try:
        async with postgres.untrusted_read_only_transaction() as conn:
            await conn.execute(text(f"SET LOCAL statement_timeout = {_STATEMENT_TIMEOUT_MS}"))
            result = await conn.stream(text(query))
            columns = [str(column) for column in result.keys()]
            fetched = await result.fetchmany(MAX_ROWS + 1)
    except Exception as exc:
        logger.warning(
            "Read-only SQL query failed",
            extra={"error_type": type(exc).__name__},
        )
        return {"ok": False, "error": "The database rejected the read-only query."}

    return rows_response(columns, fetched)


def rows_response(columns: Sequence[str], fetched: Sequence[Sequence[object]]) -> dict[str, object]:
    """``columns`` and up to ``MAX_ROWS`` of ``fetched`` as JSON; one row past it marks truncation."""
    rows: list[list[object]] = []
    response: dict[str, object] = {
        "ok": True,
        "columns": list(columns),
        "rows": rows,
        "row_count": 0,
        "truncated": len(fetched) > MAX_ROWS,
    }
    for record in fetched[:MAX_ROWS]:
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
