"""Installation administrators can query audit history without modifying it."""

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, ValidationError

from agent.audit_logs.models import AuditLogsCursor, AuditLogsPage
from agent.audit_logs.store import list_logs
from agent.dashboard.deps import admin_session

router = APIRouter(tags=["audit-logs"], dependencies=[Depends(admin_session)])


@router.get("/audit-logs")
async def api_list_audit_logs(
    start_time: AwareDatetime,
    end_time: AwareDatetime,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    operation_name: Annotated[str | None, Query(max_length=128)] = None,
    user_id: UUID | None = None,
    api_key_id: str | None = None,
    workspace_id: UUID | None = None,
) -> AuditLogsPage:
    if start_time > end_time or end_time - start_time > timedelta(days=31):
        raise HTTPException(400, "Choose an ordered time range of at most 31 days")
    try:
        after = AuditLogsCursor.model_validate_json(cursor) if cursor else None
    except ValidationError as exc:
        raise HTTPException(400, "Invalid audit log cursor") from exc
    return await list_logs(
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        cursor=after,
        operation_name=operation_name,
        user_id=user_id,
        api_key_id=api_key_id,
        workspace_id=workspace_id,
    )
