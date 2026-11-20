"""Announcements and their per-person deliveries; see migration 0058 for the lifecycle."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Literal, Self
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ForeignKey, Text, case, exists, func, insert, literal, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.ui_invalidations.topics import Topic
from openswe.users.models import User, UserIdentity

DeliveryStatus = Literal["queued", "sending", "sent", "failed"]
TEXT_MAX_CHARS = 3000


class AnnouncementDelivery(Base):
    __tablename__ = "announcement_delivery"

    announcement_id: Mapped[UUID] = mapped_column(
        ForeignKey("announcement.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    slack_user_id: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[DeliveryStatus] = mapped_column(Text, server_default="queued", default="queued")
    error: Mapped[str] = mapped_column(server_default="", default="")
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class ApprovalView(BaseModel):
    id: UUID
    thread_id: str
    text: str
    recipients: int
    payload_digest: str
    created_at: datetime
    expires_at: datetime


class AnnouncementSummary(BaseModel):
    id: UUID
    text: str
    author: str
    created_at: datetime
    finished_at: datetime | None
    recipients: int
    sent: int
    failed: int


def _reachable():
    """People with a Slack identity, the only ones the bot can DM."""
    return exists().where(UserIdentity.user_id == User.id, UserIdentity.provider == "slack")


class Announcement(Base):
    __tablename__ = "announcement"

    text: Mapped[str]
    author_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    @staticmethod
    async def recipient_count() -> int:
        async with postgres.session() as session:
            return await session.scalar(select(func.count(User.id)).where(_reachable())) or 0

    @classmethod
    async def create(cls, text: str, author_id: UUID | None) -> Self:
        """Record the announcement with everyone reachable right now as its recipients."""
        async with postgres.session() as session:
            return await cls.create_in(session, text, author_id)

    @classmethod
    async def create_in(
        cls, session: AsyncSession, text: str, author_id: UUID | None, *, freeze_slack: bool = False
    ) -> Self:
        announcement = cls(text=text, author_id=author_id)
        session.add(announcement)
        await session.flush()
        if freeze_slack:
            slack_identity = (
                select(UserIdentity.external_id)
                .where(UserIdentity.user_id == User.id, UserIdentity.provider == "slack")
                .order_by(UserIdentity.external_id)
                .limit(1)
                .scalar_subquery()
            )
            await session.execute(
                insert(AnnouncementDelivery).from_select(
                    ["announcement_id", "user_id", "slack_user_id"],
                    select(literal(announcement.id), User.id, slack_identity).where(_reachable()),
                )
            )
        else:
            await session.execute(
                insert(AnnouncementDelivery).from_select(
                    ["announcement_id", "user_id"],
                    select(literal(announcement.id), User.id).where(_reachable()),
                )
            )
        await Topic.ANNOUNCEMENTS.invalidate(session)
        return announcement

    async def recipients(self) -> int:
        async with postgres.session() as session:
            return (
                await session.scalar(
                    select(func.count()).where(AnnouncementDelivery.announcement_id == self.id)
                )
                or 0
            )

    @classmethod
    async def get(cls, announcement_id: UUID) -> Self | None:
        async with postgres.session() as session:
            return await session.get(cls, announcement_id)

    @classmethod
    async def delete(cls, announcement_id: UUID) -> None:
        async with postgres.session() as session:
            announcement = await session.get(cls, announcement_id)
            if announcement is not None:
                await session.delete(announcement)
                await Topic.ANNOUNCEMENTS.invalidate(session)

    @classmethod
    async def recent(cls, limit: int = 20) -> list[AnnouncementSummary]:
        counted = func.count(AnnouncementDelivery.user_id)

        def tally(status: DeliveryStatus):
            return func.count(case((AnnouncementDelivery.status == status, 1)))

        query = (
            select(
                cls,
                func.coalesce(User.display_name, ""),
                counted,
                tally("sent"),
                tally("failed"),
            )
            .outerjoin(User, User.id == cls.author_id)
            .outerjoin(AnnouncementDelivery, AnnouncementDelivery.announcement_id == cls.id)
            .where(
                ~exists().where(
                    AnnouncementApproval.announcement_id == cls.id,
                    AnnouncementApproval.status != "approved",
                )
            )
            .group_by(cls.id, User.display_name)
            .order_by(cls.created_at.desc())
            .limit(limit)
        )
        async with postgres.session() as session:
            rows = (await session.execute(query)).all()
        return [
            AnnouncementSummary(
                id=announcement.id,
                text=announcement.text,
                author=author,
                created_at=announcement.created_at or datetime.min,
                finished_at=announcement.finished_at,
                recipients=recipients,
                sent=sent,
                failed=failed,
            )
            for announcement, author, recipients, sent, failed in rows
        ]

    async def may_deliver(self) -> bool:
        async with postgres.session() as session:
            status = await session.scalar(
                select(AnnouncementApproval.status).where(
                    AnnouncementApproval.announcement_id == self.id
                )
            )
            return status is None or status == "approved"

    async def abandon_interrupted(self) -> int:
        """Fail deliveries a previous attempt left mid-send: their DM may already be out."""
        async with postgres.session() as session:
            abandoned = await session.scalars(
                update(AnnouncementDelivery)
                .where(
                    AnnouncementDelivery.announcement_id == self.id,
                    AnnouncementDelivery.status == "sending",
                )
                .values(status="failed", error="interrupted", updated_at=func.clock_timestamp())
                .returning(AnnouncementDelivery.user_id),
                execution_options={"synchronize_session": False},
            )
            return len(abandoned.all())

    async def claim_next(self) -> UUID | None:
        """Move one queued recipient to ``sending`` and return them; ``None`` when none are left."""
        pending = (
            select(AnnouncementDelivery.user_id)
            .where(
                AnnouncementDelivery.announcement_id == self.id,
                AnnouncementDelivery.status == "queued",
            )
            .order_by(AnnouncementDelivery.user_id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        async with postgres.session() as session:
            return await session.scalar(
                update(AnnouncementDelivery)
                .where(
                    AnnouncementDelivery.announcement_id == self.id,
                    AnnouncementDelivery.user_id == pending,
                )
                .values(status="sending", updated_at=func.clock_timestamp())
                .returning(AnnouncementDelivery.user_id),
                execution_options={"synchronize_session": False},
            )

    async def slack_recipient(self, user_id: UUID) -> str:
        async with postgres.session() as session:
            saved = await session.scalar(
                select(AnnouncementDelivery.slack_user_id).where(
                    AnnouncementDelivery.announcement_id == self.id,
                    AnnouncementDelivery.user_id == user_id,
                )
            )
            if saved is None:
                user = await User.get(user_id)
                return user.slack_user_id if user else ""
            linked = await session.scalar(
                select(UserIdentity.external_id).where(
                    UserIdentity.user_id == user_id,
                    UserIdentity.provider == "slack",
                    UserIdentity.external_id == saved,
                )
            )
            return linked or ""

    async def record(self, user_id: UUID, status: DeliveryStatus, error: str = "") -> None:
        async with postgres.session() as session:
            await session.execute(
                update(AnnouncementDelivery)
                .where(
                    AnnouncementDelivery.announcement_id == self.id,
                    AnnouncementDelivery.user_id == user_id,
                )
                .values(status=status, error=error, updated_at=func.clock_timestamp()),
                execution_options={"synchronize_session": False},
            )
            await Topic.ANNOUNCEMENTS.invalidate(session)

    async def finish(self) -> None:
        async with postgres.session() as session:
            await session.execute(
                update(Announcement)
                .where(Announcement.id == self.id)
                .values(
                    finished_at=func.coalesce(Announcement.finished_at, func.clock_timestamp())
                ),
                execution_options={"synchronize_session": False},
            )
            await Topic.ANNOUNCEMENTS.invalidate(session)


class AnnouncementApproval(Base):
    __tablename__ = "announcement_approval"

    thread_id: Mapped[str]
    announcement_id: Mapped[UUID] = mapped_column(ForeignKey("announcement.id", ondelete="CASCADE"))
    recipient_count: Mapped[int]
    payload_digest: Mapped[str]
    status: Mapped[str]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)

    @staticmethod
    async def digest(
        session: AsyncSession, announcement_id: UUID, *, lock: bool = False
    ) -> str | None:
        message_query = select(Announcement.text).where(Announcement.id == announcement_id)
        recipient_query = (
            select(AnnouncementDelivery.user_id, AnnouncementDelivery.slack_user_id)
            .where(AnnouncementDelivery.announcement_id == announcement_id)
            .order_by(AnnouncementDelivery.user_id)
        )
        if lock:
            message_query = message_query.with_for_update()
            recipient_query = recipient_query.with_for_update()
        message = await session.scalar(message_query)
        if message is None:
            return None
        recipients = (await session.execute(recipient_query)).all()
        payload = [message, [[str(user_id), slack_id] for user_id, slack_id in recipients]]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    @classmethod
    async def prepare(cls, thread_id: str, author_id: UUID, message: str) -> ApprovalView:
        async with postgres.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": thread_id}
            )
            await session.execute(
                update(cls)
                .where(cls.thread_id == thread_id, cls.status == "pending")
                .values(
                    status="superseded", updated_by=author_id, updated_at=func.clock_timestamp()
                )
            )
            announcement = await Announcement.create_in(
                session, message, author_id, freeze_slack=True
            )
            count = (
                await session.scalar(
                    select(func.count()).where(
                        AnnouncementDelivery.announcement_id == announcement.id
                    )
                )
                or 0
            )
            digest = await cls.digest(session, announcement.id)
            if digest is None:
                raise RuntimeError("Announcement disappeared during preparation")
            approval = cls(
                thread_id=thread_id,
                announcement_id=announcement.id,
                recipient_count=count,
                payload_digest=digest,
                status="pending",
                created_by=author_id,
                updated_by=author_id,
            )
            session.add(approval)
            await session.flush()
            return ApprovalView(
                id=approval.id,
                thread_id=thread_id,
                text=message,
                recipients=count,
                payload_digest=digest,
                created_at=approval.created_at,
                expires_at=approval.created_at + timedelta(hours=24),
            )

    @classmethod
    async def pending(cls, author_id: UUID) -> list[ApprovalView]:
        async with postgres.session() as session:
            rows = (
                await session.execute(
                    select(cls, Announcement.text)
                    .join(Announcement, Announcement.id == cls.announcement_id)
                    .where(
                        cls.created_by == author_id,
                        cls.status == "pending",
                        cls.created_at > func.clock_timestamp() - timedelta(hours=24),
                    )
                    .order_by(cls.created_at.desc())
                )
            ).all()
            return [
                ApprovalView(
                    id=item.id,
                    thread_id=item.thread_id,
                    text=message,
                    recipients=item.recipient_count,
                    payload_digest=item.payload_digest,
                    created_at=item.created_at,
                    expires_at=item.created_at + timedelta(hours=24),
                )
                for item, message in rows
            ]

    @classmethod
    async def thread_for(cls, approval_id: UUID, author_id: UUID) -> str | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls.thread_id).where(cls.id == approval_id, cls.created_by == author_id)
            )

    @classmethod
    async def decide(
        cls, approval_id: UUID, author_id: UUID, approved: bool, payload_digest: str
    ) -> str:
        from openswe.announcements.authorization import ApprovalAuthorization
        from openswe.announcements.delivery import ANNOUNCEMENT_TASK
        from openswe.jobs import queue

        async with postgres.session() as session:
            item = await session.scalar(select(cls).where(cls.id == approval_id).with_for_update())
            if item is None or item.created_by != author_id:
                return "missing"
            if not await ApprovalAuthorization.permits(
                item.thread_id, author_id, conn=await session.connection()
            ):
                return "missing"
            if item.status != "pending":
                return item.status
            if item.created_at <= datetime.now(UTC) - timedelta(hours=24):
                return "expired"
            if approved:
                count = (
                    await session.scalar(
                        select(func.count()).where(
                            AnnouncementDelivery.announcement_id == item.announcement_id
                        )
                    )
                    or 0
                )
                if (
                    count != item.recipient_count
                    or payload_digest != item.payload_digest
                    or await cls.digest(session, item.announcement_id, lock=True)
                    != item.payload_digest
                ):
                    return "stale"
                await queue.schedule(
                    ANNOUNCEMENT_TASK,
                    {"announcement_id": str(item.announcement_id)},
                    metadata={
                        "kind": ANNOUNCEMENT_TASK,
                        "announcement_id": str(item.announcement_id),
                    },
                    key=f"announcement:{item.announcement_id}",
                    by=author_id,
                    replace=False,
                    conn=await session.connection(),
                )
            item.status = "approved" if approved else "rejected"
            item.updated_by = author_id
            item.updated_at = datetime.now(UTC)
            await Topic.ANNOUNCEMENTS.invalidate(session)
            return item.status
