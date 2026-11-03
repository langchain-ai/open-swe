"""Which Open SWE people own a pull request's changed files, cached by a job.

A cached pull request is taken in again once it changed after the cache did, or
``CACHE_TTL`` passed, which catches CODEOWNERS and team edits.
"""

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, and_, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.repositories import Repository
from openswe.human_review.picking import Coverage
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.users import User

logger = logging.getLogger(__name__)

REFRESH_JOB: Final = "code_owner_cache"
CACHE_TTL: Final = timedelta(hours=6)


class CodeOwnerCacheUser(Base):
    __tablename__ = "code_owner_cache_user"

    cache_id: Mapped[UUID] = mapped_column(ForeignKey("code_owner_cache.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The fewest other people, besides the author, who own any changed file this person owns.
    alternatives: Mapped[int]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(default=None, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(default=None, init=False)


class CodeOwnerCache(Base):
    __tablename__ = "code_owner_cache"

    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repository.id", ondelete="CASCADE"))
    number: Mapped[int]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(default=None, init=False)
    updated_by: Mapped[UUID | None] = mapped_column(default=None, init=False)

    @classmethod
    async def alternatives_for(
        cls, user_id: UUID, pulls: Mapping[tuple[str, int], datetime | None]
    ) -> dict[tuple[str, int], int]:
        """The cached PRs this person owns files of, with their alternatives; stale ones refresh.

        ``pulls`` maps lowercased ``(repo, number)`` to when GitHub last updated it, if known.
        """
        if not pulls or not postgres.configured():
            return {}
        async with postgres.session() as session:
            rows = await session.execute(
                select(Repository.key, cls.number, cls.updated_at, CodeOwnerCacheUser.alternatives)
                .join(Repository, Repository.id == cls.repository_id)
                .outerjoin(
                    CodeOwnerCacheUser,
                    and_(
                        CodeOwnerCacheUser.cache_id == cls.id, CodeOwnerCacheUser.user_id == user_id
                    ),
                )
                .where(tuple_(Repository.key, cls.number).in_(list(pulls)))
            )
            cached = {(key, number): (updated_at, alts) for key, number, updated_at, alts in rows}
        oldest = datetime.now(UTC) - CACHE_TTL
        for pull, changed in pulls.items():
            updated_at = cached[pull][0] if pull in cached else None
            if updated_at is None or updated_at < max(changed or oldest, oldest):
                repo, number = pull
                await queue.schedule(
                    REFRESH_JOB,
                    {"repo": repo, "number": number},
                    key=f"{REFRESH_JOB}:{repo}:{number}",
                    replace=False,
                )
        return {pull: alts for pull, (_, alts) in cached.items() if alts is not None}

    @classmethod
    async def refresh(cls, repo: str, number: int) -> None:
        """Read the PR's code owners from GitHub as the App and replace what is cached."""
        owner, name = repo.split("/", 1)
        try:
            async with PullRequestClient.as_app(owner, name, number) as pull:
                payload = await pull.pull()
                base, author = payload.get("base"), payload.get("user")
                base_ref = base.get("ref") if isinstance(base, Mapping) else None
                login = author.get("login") if isinstance(author, Mapping) else None
                coverage = await Coverage.of_pull(
                    pull, base_ref if isinstance(base_ref, str) else None
                )
        except GitHubAppUnavailable:
            logger.info("No GitHub App token to cache code owners", extra={"repository": repo})
            return
        login = login if isinstance(login, str) else ""
        coverage = coverage or Coverage(())
        owners = sorted(set(coverage.owned()) - {login.lower()})
        users = await asyncio.gather(*(User.for_login("github", owner) for owner in owners))
        found = {
            user.id: coverage.alternatives(owner, login)
            for owner, user in zip(owners, users, strict=True)
            if user is not None
        }
        async with postgres.session() as session:
            repository_id = await Repository.ensure_id(session, repo)
            upsert = insert(cls).values(id=uuid7(), repository_id=repository_id, number=number)
            cache_id = (
                await session.execute(
                    upsert.on_conflict_do_update(
                        index_elements=[cls.repository_id, cls.number],
                        set_={"updated_at": func.clock_timestamp(), "updated_by": None},
                    ).returning(cls.id)
                )
            ).scalar_one()
            await session.execute(
                delete(CodeOwnerCacheUser).where(CodeOwnerCacheUser.cache_id == cache_id)
            )
            if found:
                await session.execute(
                    insert(CodeOwnerCacheUser).values(
                        [
                            {
                                "id": uuid7(),
                                "cache_id": cache_id,
                                "user_id": user_id,
                                "alternatives": alternatives,
                            }
                            for user_id, alternatives in found.items()
                        ]
                    )
                )


async def refresh_code_owners(job: Job) -> None:
    repo, number = job.payload.get("repo"), job.payload.get("number")
    if not isinstance(repo, str) or "/" not in repo or not isinstance(number, int):
        raise ValueError("A code owner refresh needs a repository and pull request number")
    await CodeOwnerCache.refresh(repo, number)
