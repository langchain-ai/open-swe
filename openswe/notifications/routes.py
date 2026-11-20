"""A person's pending app notifications and delivery receipts."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from openswe.dashboard.deps import DashboardReadDep
from openswe.dashboard.oauth import DESKTOP_APP_ORIGINS, request_origin
from openswe.notifications.service import AppNotification

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def api_notifications(request: Request, reader: DashboardReadDep) -> list[AppNotification]:
    user = reader.user
    if request_origin(request) in DESKTOP_APP_ORIGINS:
        await user.note_desktop_use(session=reader.session)
    return await AppNotification.pending(user.id, session=reader.session)


@router.post("/{notification_id}/delivered")
async def api_notification_delivered(
    notification_id: UUID, reader: DashboardReadDep
) -> dict[str, bool]:
    user = reader.user
    if not await AppNotification.acknowledge(user.id, notification_id, session=reader.session):
        raise HTTPException(404, "notification not found")
    return {"ok": True}
