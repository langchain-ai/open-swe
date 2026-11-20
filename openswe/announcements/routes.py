"""Admin routes for DMing an announcement to everyone Open SWE can reach on Slack."""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from openswe.announcements.authorization import ApprovalAuthorization
from openswe.announcements.delivery import send_announcement_dm, start_announcement
from openswe.announcements.models import (
    TEXT_MAX_CHARS,
    Announcement,
    AnnouncementApproval,
    AnnouncementSummary,
    ApprovalView,
)
from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP
from openswe.dashboard.oauth import session_user_id
from openswe.dashboard.principal import DashboardPrincipal
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
    approvals: list[ApprovalView]


class AnnouncementSent(BaseModel):
    test: bool
    recipients: int


class AnnouncementDecision(BaseModel):
    payload_digest: str


@router.get("")
async def api_list_announcements(admin: dict[str, Any] = ADMIN_DEP) -> AnnouncementsView:
    principal = DashboardPrincipal.from_session(admin)
    author = await User.for_session(session_user_id(admin), principal.github_login or "")
    approvals = await AnnouncementApproval.pending(author.id) if author else []
    eligible = [
        item
        for item in approvals
        if author and await ApprovalAuthorization.permits(item.thread_id, author.id)
    ]
    return AnnouncementsView(
        recipients=await Announcement.recipient_count(),
        items=await Announcement.recent(),
        approvals=eligible,
    )


@router.post("/approvals/{approval_id}/{decision}")
@audit_endpoint
async def api_decide_announcement(
    approval_id: UUID, decision: str, body: AnnouncementDecision, admin: dict[str, Any] = ADMIN_DEP
) -> dict[str, str]:
    if decision not in {"approve", "reject"}:
        raise HTTPException(404, "decision not found")
    principal = DashboardPrincipal.from_session(admin)
    author = await User.for_session(session_user_id(admin), principal.github_login or "")
    thread_id = await AnnouncementApproval.thread_for(approval_id, author.id) if author else None
    if not author or not thread_id or not await ApprovalAuthorization.permits(thread_id, author.id):
        raise HTTPException(404, "approval not found")
    status = await AnnouncementApproval.decide(
        approval_id, author.id, decision == "approve", body.payload_digest
    )
    if status in {"missing", "expired", "stale", "superseded"}:
        raise HTTPException(409, f"approval is {status}; prepare a new request")
    return {"status": status}


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
