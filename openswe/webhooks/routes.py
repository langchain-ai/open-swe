"""Installation administrators can query the inbound event log."""

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime

from openswe.dashboard.deps import admin_session
from openswe.webhooks.event_log import (
    EventLog,
    EventLogPage,
    EventLogQuery,
    EventUser,
    WebhookSource,
)

router = APIRouter(tags=["event-log"], dependencies=[Depends(admin_session)])


@router.get("/admin/event-log")
async def api_search_event_log(
    start_time: AwareDatetime,
    end_time: AwareDatetime,
    source: WebhookSource | None = None,
    event_type: Annotated[str, Query(max_length=256)] = "",
    event_type_prefix: Annotated[str, Query(max_length=256)] = "",
    delivery_id: Annotated[str, Query(max_length=256)] = "",
    user_id: UUID | None = None,
    workspace_id: UUID | None = None,
    repository_id: UUID | None = None,
    pull_request_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=128, pattern=r"^[^|]+\|\(\d+,\d+\)$")] = None,
) -> EventLogPage:
    if start_time > end_time or end_time - start_time > timedelta(days=15):
        raise HTTPException(400, "Choose an ordered time range of at most 15 days")
    if cursor:
        try:
            datetime.fromisoformat(cursor.partition("|")[0])
        except ValueError as exc:
            raise HTTPException(400, "Invalid event log cursor") from exc
    if (event_type or delivery_id) and source is None:
        raise HTTPException(400, "Choose a source to filter by event type or delivery ID")
    return await EventLog.search(
        EventLogQuery(
            start_time=start_time,
            end_time=end_time,
            source=source,
            event_type=event_type,
            event_type_prefix=event_type_prefix,
            delivery_id=delivery_id,
            user_id=user_id,
            workspace_id=workspace_id,
            repository_id=repository_id,
            pull_request_id=pull_request_id,
            limit=limit,
            cursor=cursor,
        )
    )


@router.get("/admin/event-log/users")
async def api_event_log_users(
    q: Annotated[str, Query(min_length=1, max_length=256)],
) -> list[EventUser]:
    query = q.strip()
    if not query:
        raise HTTPException(422, "Enter a name, login or email to search")
    return await EventLog.users(query)
