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

from pydantic import BaseModel, ConfigDict, computed_field
from sqlalchemy import text

from agent.database import postgres

logger = logging.getLogger(__name__)

ApiKeyStatus = Literal["active", "expired", "revoked"]

KEY_PREFIX = "osk_"
SECRET_BYTES = 30
SUFFIX_CHARS = 6
MAX_EXPIRY_DAYS = 365
NAME_MAX_CHARS = 200

_COLUMNS = (
    "id, workspace, name, key_suffix, created_by, created_at, expires_at, last_used_at, revoked_at"
)


class ApiKey(BaseModel):
    """One stored key. Never carries the secret."""

    model_config = ConfigDict(frozen=True)

    id: str
    workspace: str
    name: str
    key_suffix: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @computed_field
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
        async with postgres.transaction() as conn:
            row = (
                (
                    await conn.execute(
                        text(f"""
                            INSERT INTO api_key
                                (id, workspace, name, key_hash, key_suffix, created_by, expires_at)
                            VALUES
                                (:id, :workspace, :name, :key_hash, :key_suffix, :created_by,
                                 :expires_at)
                            RETURNING {_COLUMNS}
                        """),
                        {
                            "id": uuid.uuid4().hex,
                            "workspace": workspace,
                            "name": name,
                            "key_hash": hashlib.sha256(secret.encode()).hexdigest(),
                            "key_suffix": secret[-SUFFIX_CHARS:],
                            "created_by": created_by,
                            "expires_at": expires_at,
                        },
                    )
                )
                .mappings()
                .one()
            )
        return cls.model_validate(dict(row)), secret

    @classmethod
    async def list_all(cls, workspace: str | None = None) -> list[Self]:
        scope = "WHERE workspace = :workspace" if workspace is not None else ""
        async with postgres.connection() as conn:
            rows = (
                (
                    await conn.execute(
                        text(f"SELECT {_COLUMNS} FROM api_key {scope} ORDER BY created_at DESC"),
                        {"workspace": workspace} if workspace is not None else {},
                    )
                )
                .mappings()
                .all()
            )
        return [cls.model_validate(dict(row)) for row in rows]

    @classmethod
    async def revoke(cls, key_id: str) -> bool:
        """Revoke a key, keeping the first revocation time. ``False`` when unknown."""
        async with postgres.transaction() as conn:
            row = (
                await conn.execute(
                    text("""
                        UPDATE api_key
                        SET revoked_at = COALESCE(revoked_at, clock_timestamp())
                        WHERE id = :id
                        RETURNING id
                    """),
                    {"id": key_id},
                )
            ).first()
        return row is not None

    @classmethod
    async def authenticate(cls, presented: str) -> Self | None:
        """The active key behind a presented token, or ``None``.

        Revoked, expired, and unknown all answer ``None``: callers must not be
        able to tell them apart.
        """
        digest = cls.digest(presented)
        if digest is None:
            return None
        async with postgres.connection() as conn:
            row = (
                (
                    await conn.execute(
                        text(f"SELECT {_COLUMNS} FROM api_key WHERE key_hash = :key_hash"),
                        {"key_hash": digest},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        key = cls.model_validate(dict(row))
        return key if key.status == "active" else None

    @classmethod
    async def touch(cls, key_id: str) -> None:
        async with postgres.transaction() as conn:
            await conn.execute(
                text("UPDATE api_key SET last_used_at = clock_timestamp() WHERE id = :id"),
                {"id": key_id},
            )
