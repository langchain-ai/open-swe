"""Repositories: the owner every pull request belongs to.

Rows carry a synthetic UUIDv7 ``id``; the natural key is the lowercased
``owner/name``, unique, because GitHub resolves repository paths
case-insensitively. ``full_name`` keeps the casing GitHub reported for display.
"""

import logging
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from sqlalchemy import BigInteger, case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.review.styles import normalize_repo_full_name

logger = logging.getLogger(__name__)


class Repository(Base):
    __tablename__ = "repository"

    full_name: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    key: Mapped[str] = mapped_column(unique=True, init=False)
    private: Mapped[bool | None] = mapped_column(default=None)
    default_branch: Mapped[str] = mapped_column(server_default="", default="")
    first_seen_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    last_activity_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    # GitHub's numeric id survives renames. ``github_checked_at`` is when the App
    # installation's listing last looked for this name, whether or not it matched.
    github_id: Mapped[int | None] = mapped_column(BigInteger, default=None, init=False)
    github_checked_at: Mapped[datetime | None] = mapped_column(default=None, init=False)

    def __post_init__(self) -> None:
        self.full_name = normalize_repo_full_name(self.full_name)
        self.key = self.full_name.lower()

    @classmethod
    async def get(cls, full_name: str) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(cls.key == normalize_repo_full_name(full_name).lower())
            )

    @classmethod
    async def all(cls) -> list[Self]:
        async with postgres.session() as session:
            return list(await session.scalars(select(cls).order_by(cls.key)))

    @classmethod
    async def by_keys(cls, keys: Iterable[str]) -> dict[str, Self]:
        """The stored rows among ``keys`` (lowercased ``owner/name``), by key."""
        async with postgres.session() as session:
            rows = await session.scalars(select(cls).where(cls.key.in_(list(keys))))
            return {row.key: row for row in rows}

    @classmethod
    async def record_github_ids(
        cls, github_ids: Mapping[str, int | None], *, checked_at: datetime
    ) -> None:
        """Store what a lookup found for each key; keys without a row are skipped."""
        async with postgres.session() as session:
            for key, github_id in github_ids.items():
                await session.execute(
                    update(cls)
                    .where(cls.key == key)
                    .values(github_id=github_id, github_checked_at=checked_at)
                )

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
        cls = type(self)
        upsert = insert(cls).values(
            id=self.id,
            key=self.key,
            full_name=self.full_name,
            private=self.private,
            default_branch=self.default_branch,
        )
        stored = await session.scalars(
            upsert.on_conflict_do_update(
                index_elements=[cls.key],
                set_={
                    "private": func.coalesce(upsert.excluded.private, cls.private),
                    "default_branch": case(
                        (upsert.excluded.default_branch != "", upsert.excluded.default_branch),
                        else_=cls.default_branch,
                    ),
                    "last_activity_at": func.clock_timestamp(),
                },
            ).returning(cls),
            execution_options={"populate_existing": True},
        )
        return stored.one()
