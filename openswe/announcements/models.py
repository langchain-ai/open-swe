"""Announcements and their per-person deliveries; see migration 0058 for the lifecycle."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ForeignKey, Text, case, exists, func, insert, literal, select, update
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
    status: Mapped[DeliveryStatus] = mapped_column(Text, server_default="queued", default="queued")
    error: Mapped[str] = mapped_column(server_default="", default="")
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


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
        announcement = cls(text=text, author_id=author_id)
        async with postgres.session() as session:
            session.add(announcement)
            await session.flush()
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
