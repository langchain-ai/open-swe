"""The ``api_key`` record: one key, scoped to one workspace, with an expiry.

The secret is returned once, by :meth:`ApiKey.create`, and never stored: the row
keeps its SHA-256 digest and the last six characters, so a key is matched by
hashing what the caller presented and looking the digest up whole.
"""

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Literal, Self

from sqlalchemy import func, select, update
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base

logger = logging.getLogger(__name__)

ApiKeyStatus = Literal["active", "expired", "revoked"]

KEY_PREFIX = "osk_"
SECRET_BYTES = 30
SUFFIX_CHARS = 6
MAX_EXPIRY_DAYS = 365
NAME_MAX_CHARS = 200


class ApiKey(Base):
    """One stored key. Never carries the secret."""

    __tablename__ = "api_key"

    workspace: Mapped[str]
    name: Mapped[str]
    key_hash: Mapped[str] = mapped_column(unique=True)
    key_suffix: Mapped[str]
    created_by: Mapped[str]
    expires_at: Mapped[datetime]
    id: Mapped[str] = mapped_column(primary_key=True, default_factory=lambda: uuid.uuid4().hex)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)

    @property
    def status(self) -> ApiKeyStatus:
        if self.revoked_at is not None:
            return "revoked"
        return "active" if self.expires_at > datetime.now(UTC) else "expired"

    @classmethod
    def digest(cls, presented: str) -> str | None:
        """SHA-256 hex of a presented token, or ``None`` when it is not one of ours."""
        token = presented.strip()
        if not token.startswith(KEY_PREFIX):
            return None
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    async def create(
        cls, *, workspace: str, name: str, expires_at: datetime, created_by: str
    ) -> tuple[Self, str]:
        """Mint a key, returning the record and the plaintext secret exactly once."""
        secret = KEY_PREFIX + secrets.token_urlsafe(SECRET_BYTES)
        key = cls(
            workspace=workspace,
            name=name,
            key_hash=hashlib.sha256(secret.encode()).hexdigest(),
            key_suffix=secret[-SUFFIX_CHARS:],
            created_by=created_by,
            expires_at=expires_at,
        )
        async with postgres.session() as session:
            session.add(key)
            await session.flush()
        return key, secret

    @classmethod
    async def list_all(cls, workspace: str | None = None) -> list[Self]:
        query = select(cls).order_by(cls.created_at.desc())
        if workspace is not None:
            query = query.where(cls.workspace == workspace)
        async with postgres.session() as session:
            return list(await session.scalars(query))

    @classmethod
    async def revoke(cls, key_id: str) -> bool:
        """Revoke a key, keeping the first revocation time. ``False`` when unknown."""
        async with postgres.session() as session:
            revoked = await session.scalar(
                update(cls)
                .where(cls.id == key_id)
                .values(revoked_at=func.coalesce(cls.revoked_at, func.clock_timestamp()))
                .returning(cls.id),
                execution_options={"synchronize_session": False},
            )
        return revoked is not None

    @classmethod
    async def authenticate(cls, presented: str) -> Self | None:
        """The active key behind a presented token, or ``None``.

        Revoked, expired, and unknown all answer ``None``: callers must not be
        able to tell them apart.
        """
        digest = cls.digest(presented)
        if digest is None:
            return None
        async with postgres.session() as session:
            key = await session.scalar(select(cls).where(cls.key_hash == digest))
        return key if key is not None and key.status == "active" else None

    @classmethod
    async def touch(cls, key_id: str) -> None:
        async with postgres.session() as session:
            await session.execute(
                update(cls).where(cls.id == key_id).values(last_used_at=func.clock_timestamp()),
                execution_options={"synchronize_session": False},
            )
