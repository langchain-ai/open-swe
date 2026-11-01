"""The stored notification and its app/Slack delivery state."""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database.orm import NOW, Base
from openswe.utils.json_types import JsonObject


class Notification(Base):
    __tablename__ = "app_notification"

    recipient_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    dedupe_key: Mapped[str]
    title: Mapped[str]
    body: Mapped[str]
    url: Mapped[str]
    fallback_text: Mapped[str]
    fallback_at: Mapped[datetime]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    origin: Mapped[JsonObject | None] = mapped_column(JSONB, default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    delivered_at: Mapped[datetime | None] = mapped_column(default=None)
    fallback_sent_at: Mapped[datetime | None] = mapped_column(default=None)
    fallback_failed_at: Mapped[datetime | None] = mapped_column(default=None)
    fallback_claim: Mapped[UUID | None] = mapped_column(default=None)
    fallback_attempts: Mapped[int] = mapped_column(default=0)
