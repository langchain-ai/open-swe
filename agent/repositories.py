"""Repositories as rows: the owner every pull request row belongs to.

Keyed by the lowercased ``owner/name`` because GitHub resolves repository paths
case-insensitively; ``full_name`` keeps the casing GitHub reported for display.
"""

import logging
from datetime import datetime
from typing import Self

from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres
from agent.review.styles import normalize_repo_full_name

logger = logging.getLogger(__name__)

_COLUMNS = "key, full_name, private, default_branch, first_seen_at, last_activity_at"


class Repository(BaseModel):
    full_name: str
    private: bool | None = None
    default_branch: str = ""
    first_seen_at: datetime | None = None
    last_activity_at: datetime | None = None

    @field_validator("full_name", mode="before")
    @classmethod
    def _normalize_full_name(cls, value: str) -> str:
        return normalize_repo_full_name(value)

    @classmethod
    async def get(cls, full_name: str) -> Self | None:
        async with postgres.connection() as conn:
            row = (
                (
                    await conn.execute(
                        text(f"SELECT {_COLUMNS} FROM repository WHERE key = :key"),
                        {"key": cls(full_name=full_name).key},
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else cls.model_validate(dict(row))

    @classmethod
    async def all(cls) -> list[Self]:
        async with postgres.connection() as conn:
            rows = (
                await conn.execute(text(f"SELECT {_COLUMNS} FROM repository ORDER BY key"))
            ).mappings()
            return [cls.model_validate(dict(row)) for row in rows]

    @property
    def key(self) -> str:
        return self.full_name.lower()

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]

    async def save(self, conn: AsyncConnection | None = None) -> Self:
        """Upsert this row: known values win, unknown (``None``/empty) ones don't.

        Pass ``conn`` to join a caller's transaction; otherwise one is opened.
        """
        if conn is None:
            async with postgres.transaction() as own:
                return await self.save(own)
        row = (
            (
                await conn.execute(
                    text(
                        "INSERT INTO repository (key, full_name, private, default_branch) "
                        "VALUES (:key, :full_name, :private, :default_branch) "
                        "ON CONFLICT (key) DO UPDATE SET "
                        "private = COALESCE(EXCLUDED.private, repository.private), "
                        "default_branch = CASE WHEN EXCLUDED.default_branch <> '' "
                        "THEN EXCLUDED.default_branch ELSE repository.default_branch END, "
                        "last_activity_at = clock_timestamp() "
                        f"RETURNING {_COLUMNS}"
                    ),
                    {
                        "key": self.key,
                        "full_name": self.full_name,
                        "private": self.private,
                        "default_branch": self.default_branch,
                    },
                )
            )
            .mappings()
            .one()
        )
        return type(self).model_validate(dict(row))
