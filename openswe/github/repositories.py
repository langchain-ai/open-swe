"""Repositories: the owner every pull request belongs to.

Rows carry a synthetic UUIDv7 ``id``; the natural key is the lowercased
``owner/name``, unique, because GitHub resolves repository paths
case-insensitively. ``full_name`` keeps the casing GitHub reported for display.
"""

import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Self
from uuid import UUID, uuid7

import httpx2
from sqlalchemy import BigInteger, case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.config import ENV
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.checks import APPROVAL_CHECK_RUN_NAME
from openswe.github.ci import RequiredCheck
from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.github.repo_names import normalize_repo_full_name

logger = logging.getLogger(__name__)

_APPROVAL_CHECK_TTL = timedelta(hours=1)


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
    # Automatic review is opt-in; ``None`` means nobody has turned it on or off yet.
    review_enabled: Mapped[bool | None] = mapped_column(default=None, init=False)
    # Whether the default branch requires the ``Robin Approves`` check, and when that was read.
    approval_check_required: Mapped[bool | None] = mapped_column(default=None, init=False)
    approval_check_checked_at: Mapped[datetime | None] = mapped_column(default=None, init=False)

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
    async def ensure_id(cls, session: AsyncSession, full_name: str) -> UUID:
        """The row's id, inserting the row without touching its activity when there is none."""
        row = cls(full_name=full_name)
        upsert = insert(cls).values(id=row.id, key=row.key, full_name=row.full_name)
        result = await session.execute(
            upsert.on_conflict_do_update(
                index_elements=[cls.key], set_={"key": upsert.excluded.key}
            ).returning(cls.id)
        )
        return result.scalar_one()

    @classmethod
    async def resolve_default_branch(cls, owner: str, name: str) -> str:
        """The stored default branch, else the one the App reads from GitHub; ``""`` if neither."""
        full_name = f"{owner}/{name}"
        repository = await cls.get(full_name)
        if repository and repository.default_branch:
            return repository.default_branch
        try:
            async with GitHubClient.as_app(owner, name) as github:
                info = await github.repo(owner, name).info()
        except GitHubAppUnavailable, httpx2.HTTPError, ValueError:
            logger.warning(
                "Could not resolve repository default branch",
                extra={"repo_full_name": full_name},
                exc_info=True,
            )
            return ""
        value = info.get("default_branch")
        if isinstance(value, str) and value:
            await cls(full_name=full_name, default_branch=value).save()
            return value
        return ""

    @classmethod
    async def requires_approval_check(
        cls, owner: str, name: str, branch: str, *, include_any_source: bool = False
    ) -> bool | None:
        """Whether Robin's check is required, with separate caches for publishing and creation."""
        if include_any_source:
            from langgraph_api.cache import swr

            key = (
                f"approval-check-publishing:{owner.lower()}/{name.lower()}:"
                f"{ENV.GITHUB_APP_ID.get()}:{branch}"
            )
            try:
                result = await swr(
                    key,
                    partial(cls._read_approval_check, owner, name, branch, include_any_source=True),
                    fresh_for=_APPROVAL_CHECK_TTL,
                    max_age=timedelta(hours=24),
                )
                return result.value
            except GitHubAppUnavailable, httpx2.HTTPError, ValueError:
                logger.warning(
                    "Could not read the branch's required checks",
                    extra={"repo_full_name": f"{owner}/{name}", "branch": branch},
                    exc_info=True,
                )
                return None
        full_name = f"{owner}/{name}"
        cacheable = branch == await cls.resolve_default_branch(owner, name)
        row = await cls.get(full_name) if cacheable else None
        if (
            row is not None
            and row.approval_check_required is not None
            and row.approval_check_checked_at is not None
            and datetime.now(UTC) - row.approval_check_checked_at < _APPROVAL_CHECK_TTL
        ):
            return row.approval_check_required
        try:
            required = await cls._read_approval_check(owner, name, branch)
        except GitHubAppUnavailable, httpx2.HTTPError, ValueError:
            logger.warning(
                "Could not read the branch's required checks",
                extra={"repo_full_name": full_name, "branch": branch},
                exc_info=True,
            )
            return row.approval_check_required if row is not None else None
        if cacheable:
            async with postgres.session() as session:
                repository_id = await cls.ensure_id(session, full_name)
                await session.execute(
                    update(cls)
                    .where(cls.id == repository_id)
                    .values(
                        approval_check_required=required,
                        approval_check_checked_at=datetime.now(UTC),
                    )
                )
        return required

    @classmethod
    async def _read_approval_check(
        cls, owner: str, name: str, branch: str, *, include_any_source: bool = False
    ) -> bool:
        async with GitHubClient.as_app(owner, name) as github:
            checks = await RequiredCheck.for_branch(
                github.repo(owner, name), branch, include_review_checks=True
            )
        if checks is None:
            raise ValueError("Could not determine required checks")
        app_id = int(ENV.GITHUB_APP_ID.get() or 0)
        return any(
            check.name == APPROVAL_CHECK_RUN_NAME
            and (check.app_id == app_id or (include_any_source and check.app_id is None))
            for check in checks
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
