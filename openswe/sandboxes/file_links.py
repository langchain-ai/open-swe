"""Short links to sandbox files.

A link stores the file it opens, never a signed URL or token: each visit asks
LangSmith for a fresh download URL, so the id is the only secret a person holds.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base

_THREAD_FOREIGN_KEY = "sandbox_file_link_thread_id_fkey"

ContentDisposition = Literal["attachment", "inline"]


class SandboxFileLink(Base):
    __tablename__ = "sandbox_file_link"

    id: Mapped[str] = mapped_column(primary_key=True)
    thread_id: Mapped[str]
    sandbox_id: Mapped[str]
    path: Mapped[str]
    content_type: Mapped[str | None]
    content_disposition: Mapped[ContentDisposition] = mapped_column(Text)
    expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


async def create_link(
    *,
    thread_id: str,
    sandbox_id: str,
    path: str,
    content_type: str | None,
    content_disposition: ContentDisposition,
    expires_in_seconds: int | None,
) -> SandboxFileLink | None:
    """Store a link to ``path``, or None when ``thread_id`` has no ``thread`` row to own it.

    Threads that predate the transcript have no row, and a link must not outlive
    its thread.
    """
    link = SandboxFileLink(
        id=secrets.token_urlsafe(16),
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


async def get_link(link_id: str) -> SandboxFileLink | None:
    async with postgres.session() as session:
        return await session.get(SandboxFileLink, link_id)
