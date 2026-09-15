"""Repositories as rows: the owner every pull request row belongs to.

Rows carry a synthetic UUIDv7 ``id``; the natural key is the lowercased
``owner/name``, unique, because GitHub resolves repository paths
case-insensitively. ``full_name`` keeps the casing GitHub reported for display.
"""

import logging
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from pydantic import BaseModel, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent.database import postgres
from agent.database.rows import RepositoryRow
from agent.review.styles import normalize_repo_full_name

logger = logging.getLogger(__name__)


class Repository(BaseModel):
    full_name: str
    id: UUID | None = None
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
        async with postgres.session() as session:
            row = await session.scalar(
                select(RepositoryRow).where(RepositoryRow.key == cls(full_name=full_name).key)
            )
        return None if row is None else cls.model_validate(row, from_attributes=True)

    @classmethod
    async def all(cls) -> list[Self]:
        async with postgres.session() as session:
            rows = await session.scalars(select(RepositoryRow).order_by(RepositoryRow.key))
            return [cls.model_validate(row, from_attributes=True) for row in rows]

    @property
    def key(self) -> str:
        return self.full_name.lower()

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]

    async def save(self, session: AsyncSession | None = None) -> Self:
        """Upsert this row: known values win, unknown (``None``/empty) ones don't.

        Pass ``session`` to join a caller's transaction; otherwise one is opened.
        """
        if session is None:
            async with postgres.session() as own:
                return await self.save(own)
        upsert = insert(RepositoryRow).values(
            id=self.id or uuid7(),
            key=self.key,
            full_name=self.full_name,
            private=self.private,
            default_branch=self.default_branch,
        )
        row = (
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[RepositoryRow.key],
                    set_={
                        "private": func.coalesce(upsert.excluded.private, RepositoryRow.private),
                        "default_branch": case(
                            (upsert.excluded.default_branch != "", upsert.excluded.default_branch),
                            else_=RepositoryRow.default_branch,
                        ),
                        "last_activity_at": func.clock_timestamp(),
                    },
                ).returning(RepositoryRow)
            )
        ).scalar_one()
        return type(self).model_validate(row, from_attributes=True)
