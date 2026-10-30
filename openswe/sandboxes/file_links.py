"""Short links to sandbox files.

A link stores the file it opens, never a signed URL: each visit asks LangSmith for
a fresh download URL, so the link's token is the only secret a person holds.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, Self
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.models import User

LINKS_PATH = "/dashboard/api/l"
_THREAD_FOREIGN_KEY = "sandbox_file_link_thread_id_fkey"

ContentDisposition = Literal["attachment", "inline"]


class SandboxFileLink(Base):
    __tablename__ = "sandbox_file_link"

    token: Mapped[str]
    thread_id: Mapped[str]
    sandbox_id: Mapped[str]
    path: Mapped[str]
    content_type: Mapped[str | None]
    content_disposition: Mapped[ContentDisposition] = mapped_column(Text)
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
        *,
        thread_id: str,
        sandbox_id: str,
        path: str,
        content_type: str | None,
        content_disposition: ContentDisposition,
        expires_in_seconds: int | None,
        created_by: UUID | None,
    ) -> Self | None:
        """Store a link to ``path``, or None when ``thread_id`` has no ``thread`` row to own it.

        Threads that predate the transcript have no row, and a link must not outlive
        its thread. ``created_by`` is the person whose run asked for the link.
        """
        link = cls(
            token=secrets.token_urlsafe(16),
            thread_id=thread_id,
            sandbox_id=sandbox_id,
            path=path,
            content_type=content_type,
            content_disposition=content_disposition,
            expires_at=(
                None
                if expires_in_seconds is None
                else datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
            ),
            created_by=created_by,
            updated_by=created_by,
        )
        try:
            async with postgres.session() as session:
                session.add(link)
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
            if constraint != _THREAD_FOREIGN_KEY:
                raise
            return None
        return link

    @classmethod
    async def by_token(cls, token: str) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(select(cls).where(cls.token == token))
