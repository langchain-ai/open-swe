import secrets
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, select
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.models import User
from openswe.utils.dashboard_links import dashboard_api_base_url

LINKS_PATH = "/dashboard/api/l"


class ShortLink(Base):
    """A short URL anyone holding its token can follow to ``target_url`` until ``expires_at``."""

    __tablename__ = "short_link"

    token: Mapped[str]
    target_url: Mapped[str]
    expires_at: Mapped[datetime | None]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )

    @classmethod
    async def create(
        cls,
        target_url: str,
        *,
        expires_at: datetime | None = None,
        created_by: UUID | None = None,
    ) -> Self:
        link = cls(
            token=secrets.token_urlsafe(16),
            target_url=target_url,
            expires_at=expires_at,
            created_by=created_by,
            updated_by=created_by,
        )
        async with postgres.session() as session:
            session.add(link)
        return link

    @classmethod
    async def by_token(cls, token: str) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(select(cls).where(cls.token == token))

    @property
    def url(self) -> str:
        return f"{dashboard_api_base_url()}{LINKS_PATH}/{self.token}"
