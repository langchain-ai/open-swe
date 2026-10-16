"""Admin routes for DMing an announcement to everyone Open SWE can reach on Slack."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from openswe.announcements.delivery import send_announcement_dm, start_announcement
from openswe.announcements.models import TEXT_MAX_CHARS, Announcement, AnnouncementSummary
from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP
from openswe.dashboard.oauth import session_user_id
from openswe.slack.http import SlackRequestError
from openswe.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/announcements", tags=["announcements"])


class AnnouncementCreate(BaseModel):
    text: str = Field(min_length=1, max_length=TEXT_MAX_CHARS)
    test: bool = False

    @field_validator("text")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class AnnouncementsView(BaseModel):
    recipients: int
    items: list[AnnouncementSummary]


class AnnouncementSent(BaseModel):
    test: bool
    recipients: int


@router.get("")
async def api_list_announcements(_admin: dict[str, Any] = ADMIN_DEP) -> AnnouncementsView:
    return AnnouncementsView(
        recipients=await Announcement.recipient_count(), items=await Announcement.recent()
    )


@router.post("", status_code=202)
@audit_endpoint
async def api_send_announcement(
    body: AnnouncementCreate, admin: dict[str, Any] = ADMIN_DEP
) -> AnnouncementSent:
    """DM the announcement to everyone, or with ``test`` to the admin alone."""
    author = await User.for_session(session_user_id(admin), admin["sub"])
    if body.test:
        if author is None or not author.slack_user_id:
            raise HTTPException(400, "Link your Slack account to send yourself a test.")
        try:
            await send_announcement_dm(author.slack_user_id, body.text)
        except SlackRequestError as exc:
            raise HTTPException(502, f"Slack refused the test DM: {exc.code}") from exc
        return AnnouncementSent(test=True, recipients=1)
    try:
        recipients = await start_announcement(body.text, author.id if author else None)
    except Exception as exc:
        logger.exception("Announcement could not be started")
        raise HTTPException(502, "Could not start sending. Try again.") from exc
    return AnnouncementSent(test=False, recipients=recipients)
