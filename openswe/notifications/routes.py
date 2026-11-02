"""A person's pending app notifications and delivery receipts."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.oauth import DESKTOP_APP_ORIGIN, request_origin, session_user_id
from openswe.notifications.service import AppNotification
from openswe.users import User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def api_notifications(
    request: Request, session: dict[str, object] = SESSION_DEP
) -> list[AppNotification]:
    login = session.get("sub")
    user = await User.for_session(session_user_id(session), login if isinstance(login, str) else "")
    if user is None:
        return []
    if request_origin(request) == DESKTOP_APP_ORIGIN:
        await user.note_desktop_use()
    return await AppNotification.pending(user.id)


@router.post("/{notification_id}/delivered")
async def api_notification_delivered(
    notification_id: UUID, session: dict[str, object] = SESSION_DEP
) -> dict[str, bool]:
    login = session.get("sub")
    user = await User.for_session(session_user_id(session), login if isinstance(login, str) else "")
    if user is None or not await AppNotification.acknowledge(user.id, notification_id):
        raise HTTPException(404, "notification not found")
    return {"ok": True}
