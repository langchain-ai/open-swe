"""The token a person's Claude Code hooks present to mirror their sessions into Open SWE.

The secret is returned once, by :meth:`ClaudeCodeSyncToken.create`, and never
stored: the row keeps its SHA-256 digest and last characters. A token only
writes the owner's own mirrored sessions, so it lives until the owner revokes it,
which deletes the row.
"""

import hashlib
import secrets
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, delete, func, select, update
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.models import User

TOKEN_PREFIX = "occ_"
SECRET_BYTES = 30
SUFFIX_CHARS = 6
NAME_MAX_CHARS = 120


class ClaudeCodeSyncToken(Base):
    """One stored token. Never carries the secret."""

    __tablename__ = "claude_code_sync_token"

    user_id: Mapped[UUID] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    name: Mapped[str]
    token_hash: Mapped[str] = mapped_column(unique=True)
    token_suffix: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )

    @staticmethod
    def digest(presented: str) -> str | None:
        """SHA-256 hex of a presented token, or ``None`` when it is not one of ours."""
        token = presented.strip()
        if not token.startswith(TOKEN_PREFIX):
            return None
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    async def create(cls, *, user_id: UUID, name: str) -> tuple[Self, str]:
        """Mint a token for ``user_id``, returning the record and the plaintext secret once."""
        secret = TOKEN_PREFIX + secrets.token_urlsafe(SECRET_BYTES)
        token = cls(
            user_id=user_id,
            name=name,
            token_hash=hashlib.sha256(secret.encode()).hexdigest(),
            token_suffix=secret[-SUFFIX_CHARS:],
            created_by=user_id,
            updated_by=user_id,
        )
        async with postgres.session() as session:
            session.add(token)
            await session.flush()
            await session.refresh(token)
        return token, secret

    @classmethod
    async def for_user(cls, user_id: UUID) -> list[Self]:
        async with postgres.session() as session:
            return list(
                await session.scalars(
                    select(cls).where(cls.user_id == user_id).order_by(cls.created_at.desc())
                )
            )

    @classmethod
    async def revoke(cls, *, user_id: UUID, token_id: UUID) -> bool:
        """Delete one of ``user_id``'s tokens; ``False`` when they have no such token."""
        async with postgres.session() as session:
            deleted = await session.scalar(
                delete(cls).where(cls.id == token_id, cls.user_id == user_id).returning(cls.id),
                execution_options={"synchronize_session": False},
            )
        return deleted is not None

    @classmethod
    async def authenticate(cls, presented: str) -> Self | None:
        """The token behind ``presented``, its use recorded; ``None`` when it is not one."""
        digest = cls.digest(presented)
        if digest is None:
            return None
        async with postgres.session() as session:
            return await session.scalar(
                update(cls)
                .where(cls.token_hash == digest)
                .values(last_used_at=func.clock_timestamp())
                .returning(cls),
                execution_options={"synchronize_session": False},
            )
