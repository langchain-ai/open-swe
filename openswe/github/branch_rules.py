"""Merge rules a repository's branch enforces, cached so pull request reads skip GitHub."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid7

import httpx2
from sqlalchemy import ForeignKey, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.http import GraphQLError, RepoClient
from openswe.github.repositories import Repository
from openswe.users.models import User

logger = logging.getLogger(__name__)

BRANCH_RULE_FRESH_FOR = timedelta(hours=1)


class BranchMergeRule(Base):
    """What GitHub requires before a pull request into ``branch`` may merge."""

    __tablename__ = "branch_merge_rule"

    repository_id: Mapped[UUID] = mapped_column(ForeignKey(Repository.id, ondelete="CASCADE"))
    branch: Mapped[str]
    requires_conversation_resolution: Mapped[bool]
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
    async def cached(cls, full_name: str, branch: str) -> Self | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(cls)
                .join(Repository, Repository.id == cls.repository_id)
                .where(Repository.key == full_name.lower(), cls.branch == branch)
            )

    @classmethod
    async def requires_conversation_resolution_on(cls, repo: RepoClient, branch: str) -> bool:
        """The cached answer while fresh, else GitHub's; a failed read keeps the stale answer."""
        cached = await cls.cached(repo.full_name, branch)
        if cached is not None and datetime.now(UTC) - cached.updated_at < BRANCH_RULE_FRESH_FOR:
            return cached.requires_conversation_resolution
        try:
            required = await repo.requires_conversation_resolution(branch)
        except httpx2.HTTPError, GraphQLError, ValueError:
            logger.warning(
                "Could not read whether the branch requires conversation resolution",
                extra={"repo_full_name": repo.full_name, "branch": branch},
                exc_info=True,
            )
            return cached.requires_conversation_resolution if cached is not None else False
        await cls._store(repo.full_name, branch, required)
        return required

    @classmethod
    async def _store(cls, full_name: str, branch: str, required: bool) -> None:
        async with postgres.session() as session:
            repository_id = await Repository.ensure_id(session, full_name)
            upsert = insert(cls).values(
                id=uuid7(),
                repository_id=repository_id,
                branch=branch,
                requires_conversation_resolution=required,
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.repository_id, cls.branch],
                    set_={
                        "requires_conversation_resolution": required,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )
