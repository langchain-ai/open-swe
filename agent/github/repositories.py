"""Repositories: the owner every pull request belongs to.

Rows carry a synthetic UUIDv7 ``id``; the natural key is the lowercased
``owner/name``, unique, because GitHub resolves repository paths
case-insensitively. ``full_name`` keeps the casing GitHub reported for display.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.review.styles import normalize_repo_full_name
from agent.run_config import Repo

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
    async def get_many(cls, ids: Sequence[UUID]) -> list[Self]:
        """The rows for ``ids`` in the order given; unknown ids are skipped."""
        if not ids:
            return []
        async with postgres.session() as session:
            rows = {
                row.id: row for row in await session.scalars(select(cls).where(cls.id.in_(ids)))
            }
        return [rows[id] for id in ids if id in rows]

    @classmethod
    async def ensure(cls, full_name: str, *, private: bool | None = None) -> Self:
        """The row for ``owner/name``, created on first sight.

        Raises ``ValueError`` when ``full_name`` is not an ``owner/name``.
        """
        return await cls(full_name=full_name, private=private).save()

    @classmethod
    async def ensure_from_config(cls, raw: object) -> list[Self]:
        """The rows a ``configurable["repo"]``-shaped value names, registering each.

        Webhook payloads and stored automations still carry one repository as
        ``{"owner", "name"}``. Returns a list so callers can hand it straight to
        the code that takes a thread's repositories; a malformed value is dropped.
        """
        repo = Repo.parse(raw)
        if not repo:
            return []
        try:
            return [await cls.ensure(repo.full_name)]
        except ValueError:
            logger.warning("Ignoring malformed repository", extra={"repository": repo.full_name})
            return []

    @classmethod
    async def all(cls) -> list[Self]:
        async with postgres.session() as session:
            return list(await session.scalars(select(cls).order_by(cls.key)))

    async def update_default_branch(self, default_branch: str) -> Self:
        """Record the default branch a checkout reported; an empty value is ignored."""
        branch = default_branch.strip()
        if not branch or branch == self.default_branch:
            return self
        self.default_branch = branch
        return await self.save()

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
