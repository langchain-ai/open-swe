"""Short-lived, hashed, single-use codes that sign a phone in as their issuer."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, delete, func, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users import User

QR_LOGIN_TTL = timedelta(minutes=2)
CODE_BYTES = 32


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class QrLoginCode(Base):
    __tablename__ = "qr_login_code"

    code_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def issue(cls, user_id: UUID) -> tuple[Self, str]:
        """Mint a code for ``user_id``, returning the record and the plaintext once."""
        code = secrets.token_urlsafe(CODE_BYTES)
        record = cls(
            code_hash=_digest(code),
            expires_at=datetime.now(UTC) + QR_LOGIN_TTL,
            created_by=user_id,
            updated_by=user_id,
        )
        async with postgres.session() as session:
            await session.execute(select(User.id).where(User.id == user_id).with_for_update())
            await session.execute(
                delete(cls).where(
                    or_(cls.created_by == user_id, cls.expires_at <= func.clock_timestamp())
                ),
                execution_options={"synchronize_session": False},
            )
            session.add(record)
        return record, code

    @classmethod
    async def redeem(cls, code: str) -> UUID | None:
        """Consume a live code, returning who issued it; ``None`` when unknown or expired."""
        async with postgres.session() as session:
            return await session.scalar(
                delete(cls)
                .where(cls.code_hash == _digest(code), cls.expires_at > func.clock_timestamp())
                .returning(cls.created_by),
                execution_options={"synchronize_session": False},
            )
