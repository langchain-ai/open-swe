"""Append-only writes and keyset-paginated audit queries."""

import logging
from datetime import datetime
from uuid import UUID

import anyio
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert

from agent.audit_logs.models import AuditLog, AuditLogRow, AuditLogsCursor, AuditLogsPage
from agent.database import postgres

logger = logging.getLogger(__name__)


async def append(entry: AuditLog) -> None:
    async with postgres.transaction() as conn:
        await conn.execute(
            insert(AuditLogRow)
            .values(
                id=entry.id,
                request_time=entry.request_time,
                operation_name=entry.operation_name,
                operation_succeeded=entry.operation_succeeded,
                api_key_id=entry.api_key_id,
                user_id=entry.user_id,
                workspace_id=entry.workspace_id,
                enrichments=entry.enrichments.model_dump(mode="json", exclude_none=True),
            )
            .on_conflict_do_nothing(index_elements=[AuditLogRow.id])
        )


async def append_safely(entry: AuditLog) -> None:
    if not postgres.configured():
        return
    try:
        with anyio.fail_after(3, shield=True):
            await append(entry)
    except Exception:
        logger.exception("Could not persist audit log", extra={"audit_log_id": str(entry.id)})


async def list_logs(
    *,
    start_time: datetime,
    end_time: datetime,
    limit: int,
    cursor: AuditLogsCursor | None = None,
    operation_name: str | None = None,
    user_id: UUID | None = None,
    api_key_id: str | None = None,
    workspace_id: UUID | None = None,
) -> AuditLogsPage:
    query = select(AuditLogRow).where(
        AuditLogRow.request_time >= start_time, AuditLogRow.request_time <= end_time
    )
    if operation_name is not None:
        query = query.where(AuditLogRow.operation_name == operation_name)
    if user_id is not None:
        query = query.where(AuditLogRow.user_id == user_id)
    if api_key_id is not None:
        query = query.where(AuditLogRow.api_key_id == api_key_id)
    if workspace_id is not None:
        query = query.where(AuditLogRow.workspace_id == workspace_id)
    if cursor is not None:
        query = query.where(
            tuple_(AuditLogRow.request_time, AuditLogRow.id) < (cursor.request_time, cursor.id)
        )
    query = query.order_by(AuditLogRow.request_time.desc(), AuditLogRow.id.desc()).limit(limit + 1)
    async with postgres.session() as session:
        rows = list(await session.scalars(query))
    items = [AuditLog.model_validate(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = AuditLogsCursor(request_time=last.request_time, id=last.id).model_dump_json()
    return AuditLogsPage(items=items, cursor=next_cursor)
