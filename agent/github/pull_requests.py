"""Pull requests, owned by a repository and pointing at their threads.

A pull request is the thing Open SWE actually works on across many threads: the
agent thread that opened it, later threads that repair it, and the reviewer
thread that reviews it. Before these tables the only way back from a PR to its
threads was a paginated ``threads.search`` over ``pr_url``/``pr_urls`` metadata
followed by a heuristic pick, which cannot answer "which thread is *the* thread"
at all.

Each pull request names one ``primary`` thread — the thread that opened the PR,
or the first thread associated with it — and any number of secondaries. First
writer wins: every write upserts the pull request row first, which locks it for
the rest of the transaction, so the role assigned to each new link is decided
against links that have already committed, and a partial unique index makes a
second primary impossible.

Two kinds of write, by who knows what. ``save`` is for callers holding the PR
as GitHub describes it (the opener, lifecycle webhooks): it writes the
GitHub-owned columns and can set, never clear, ``resolves_thread``.
``link_thread`` and ``link_review`` are for callers that only know the PR's
identity: they create the row if it is missing and touch no other column.

Rows live in PostgreSQL (``POSTGRES_URI``), which this module requires. Entity
rows carry a synthetic UUIDv7 ``id``; ``(repository, number)`` stays the natural
key callers address a PR by.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid7

from pydantic import AliasPath, BaseModel, Field, ValidationError
from sqlalchemy import BigInteger, ForeignKey, Text, UniqueConstraint, desc, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.comments import PrState, derive_pr_state
from agent.github.pull_request_status import pull_request_identity
from agent.github.repositories import Repository
from agent.review.findings import REVIEWER_THREAD_KIND
from agent.users.models import UserIdentity
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

ThreadRole = Literal["primary", "secondary"]

_SEARCH_PAGE_SIZE = 50
_GITHUB_COLUMNS = ("state", "title", "head_ref", "base_ref", "author")


class ThreadLink(Base):
    __tablename__ = "pull_request_thread"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), primary_key=True, init=False
    )
    role: Mapped[ThreadRole] = mapped_column(Text, default="secondary")
    source: Mapped[str] = mapped_column(server_default="", default="")
    linked_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


class ReviewLink(Base):
    __tablename__ = "pull_request_review"

    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), init=False
    )
    reviewer_thread_id: Mapped[str] = mapped_column(server_default="", default="")
    github_review_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    url: Mapped[str] = mapped_column(server_default="", default="")
    head_sha: Mapped[str] = mapped_column(server_default="", default="")
    finding_count: Mapped[int | None] = mapped_column(default=None)
    published_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    def same_as(self, other: ReviewLink) -> bool:
        if self.github_review_id is not None:
            return other.github_review_id == self.github_review_id
        return (
            other.github_review_id is None
            and other.reviewer_thread_id == self.reviewer_thread_id
            and other.head_sha == self.head_sha
        )


class PullRequest(Base):
    __tablename__ = "pull_request"
    __table_args__ = (UniqueConstraint("repository_id", "number"),)

    owner: Mapped[str]
    repo: Mapped[str]
    number: Mapped[int]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repository.id"), init=False)
    state: Mapped[PrState] = mapped_column(Text, default="open")
    title: Mapped[str] = mapped_column(server_default="", default="")
    head_ref: Mapped[str] = mapped_column(server_default="", default="")
    base_ref: Mapped[str] = mapped_column(server_default="", default="")
    author: Mapped[str] = mapped_column(server_default="", default="")
    author_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    resolves_thread: Mapped[bool] = mapped_column(default=False)
    threads: Mapped[list[ThreadLink]] = relationship(
        default_factory=list,
        cascade="all, delete-orphan",
        order_by=lambda: (
            desc(ThreadLink.role == "primary"),
            ThreadLink.linked_at,
            ThreadLink.thread_id,
        ),
    )
    reviews: Mapped[list[ReviewLink]] = relationship(
        default_factory=list,
        cascade="all, delete-orphan",
        order_by=lambda: (ReviewLink.published_at, ReviewLink.id),
    )
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    repository: Mapped[Repository] = relationship(init=False)

    @classmethod
    async def get(cls, owner: str, repo: str, number: int) -> Self | None:
        """The stored row, or ``None`` when nothing has saved this PR yet."""
        async with postgres.session() as session:
            return await cls(owner=owner, repo=repo, number=number).fetch(session)

    @classmethod
    async def load(cls, owner: str, repo: str, number: int) -> Self:
        """The stored row, or an unsaved one, so callers always hold a record."""
        return await cls.get(owner, repo, number) or cls(owner=owner, repo=repo, number=number)

    @classmethod
    async def for_repository(cls, owner: str, repo: str) -> list[Self]:
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._with_links(select(cls))
                .join(cls.repository)
                .where(Repository.key == f"{owner}/{repo}".lower())
                .order_by(cls.number)
            )
            return list(rows)

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def primary_thread_id(self) -> str | None:
        return next((link.thread_id for link in self.threads if link.role == "primary"), None)

    @property
    def thread_ids(self) -> list[str]:
        """Linked threads, primary first."""
        return [link.thread_id for link in self.threads if link.role == "primary"] + [
            link.thread_id for link in self.threads if link.role != "primary"
        ]

    async def save(
        self, *, repository_private: bool | None = None, author_github_id: int | None = None
    ) -> Self:
        """Write the PR as GitHub describes it and register its repository.

        Overwrites the GitHub-owned columns; ``resolves_thread`` can only be set,
        never cleared. Queued ``threads``/``reviews`` are linked as well. The
        author is linked to a registered user when one matches their GitHub id
        or, failing that, their login; an unregistered author leaves it unset.
        """
        return await self._write(
            overwrite=True, repository_private=repository_private, author_github_id=author_github_id
        )

    async def link_thread(self, thread_id: str, *, source: str = "") -> Self:
        """Associate a thread with this PR, as primary when it has none yet."""
        if all(link.thread_id != thread_id for link in self.threads):
            self.threads.append(ThreadLink(thread_id=thread_id, source=source))
        return await self._write(overwrite=False)

    async def link_review(
        self,
        *,
        reviewer_thread_id: str = "",
        github_review_id: int | None = None,
        head_sha: str = "",
        finding_count: int | None = None,
    ) -> Self:
        """Record a published review against this PR; a same-identity row is updated."""
        self.reviews.append(
            ReviewLink(
                reviewer_thread_id=reviewer_thread_id,
                github_review_id=github_review_id,
                url=(
                    f"{self.url}#pullrequestreview-{github_review_id}"
                    if github_review_id is not None
                    else self.url
                ),
                head_sha=head_sha,
                finding_count=finding_count,
            )
        )
        return await self._write(overwrite=False)

    async def linked_threads(self, *, backfill: bool = True) -> list[str]:
        """Linked agent threads, primary first.

        Falls back to the legacy ``pr_url``/``pr_urls`` thread scan for pull
        requests that predate the tables, saving what it finds so the scan
        happens at most once per PR.
        """
        if self.threads or not backfill:
            return self.thread_ids
        discovered = await self.discover_threads()
        logger.info(
            "Backfilled pull request threads from legacy metadata scan",
            extra={
                "pr_repo_full_name": self.repo_full_name,
                "pr_number": self.number,
                "pr_discovered_threads": len(discovered),
            },
        )
        if not discovered:
            return []
        self.threads.extend(
            ThreadLink(thread_id=thread_id, source="backfill") for thread_id in discovered
        )
        return (await self._write(overwrite=False)).thread_ids

    async def primary_thread(self, *, backfill: bool = True) -> str | None:
        threads = await self.linked_threads(backfill=backfill)
        return threads[0] if threads else None

    async def discover_threads(self) -> Sequence[str]:
        """Agent threads whose metadata still points at this PR, oldest first."""
        client = langgraph_client()
        found: dict[str, str] = {}
        for metadata_filter in ({"pr_url": self.url}, {"pr_urls": [self.url]}):
            offset = 0
            while True:
                try:
                    page = await client.threads.search(
                        metadata=metadata_filter, limit=_SEARCH_PAGE_SIZE, offset=offset
                    )
                except Exception:
                    logger.warning(
                        "Pull request thread backfill search failed",
                        extra={"pr_url": self.url},
                        exc_info=True,
                    )
                    break
                for thread in page or []:
                    if thread_metadata(thread).get("kind") == REVIEWER_THREAD_KIND:
                        continue
                    thread_id = thread.get("thread_id")
                    if isinstance(thread_id, str) and thread_id:
                        found[thread_id] = str(thread.get("created_at") or "")
                if len(page or []) < _SEARCH_PAGE_SIZE:
                    break
                offset += _SEARCH_PAGE_SIZE
        return [thread_id for thread_id, _ in sorted(found.items(), key=lambda item: item[1])]

    async def fetch(self, session: AsyncSession) -> Self | None:
        """This PR's stored row with its threads and reviews, read through ``session``."""
        cls = type(self)
        return await session.scalar(
            cls._with_links(select(cls))
            .join(cls.repository)
            .where(Repository.key == self.repo_full_name.lower(), cls.number == self.number)
            .execution_options(populate_existing=True)
        )

    @classmethod
    def _with_links(cls, statement):  # noqa: ANN001, ANN206
        return statement.options(selectinload(cls.threads), selectinload(cls.reviews))

    async def _write(
        self,
        *,
        overwrite: bool,
        repository_private: bool | None = None,
        author_github_id: int | None = None,
    ) -> Self:
        async with postgres.session() as session:
            repository = await Repository(
                full_name=self.repo_full_name, private=repository_private
            ).save(session)
            if overwrite and self.author_user_id is None:
                self.author_user_id = await self._author_user_id(session, author_github_id)
            row = await self._upsert(session, repository.id, overwrite=overwrite)
            for link in self.threads:
                if all(existing.thread_id != link.thread_id for existing in row.threads):
                    role: ThreadRole = (
                        "secondary" if any(t.role == "primary" for t in row.threads) else "primary"
                    )
                    row.threads.append(
                        ThreadLink(thread_id=link.thread_id, role=role, source=link.source)
                    )
            for review in self.reviews:
                existing = next((r for r in row.reviews if review.same_as(r)), None)
                if existing is None:
                    row.reviews.append(
                        ReviewLink(
                            id=review.id,
                            reviewer_thread_id=review.reviewer_thread_id,
                            github_review_id=review.github_review_id,
                            url=review.url,
                            head_sha=review.head_sha,
                            finding_count=review.finding_count,
                        )
                    )
                else:
                    existing.reviewer_thread_id = review.reviewer_thread_id
                    existing.url = review.url
                    existing.head_sha = review.head_sha
                    existing.finding_count = review.finding_count
                    existing.published_at = func.clock_timestamp()
            await session.flush()
            stored = await self.fetch(session)
        if stored is None:
            raise RuntimeError(f"pull request {self.url} vanished during save")
        return stored

    async def _author_user_id(
        self, session: AsyncSession, author_github_id: int | None
    ) -> UUID | None:
        if author_github_id is not None:
            matches = UserIdentity.external_id == str(author_github_id)
        elif self.author:
            matches = func.lower(UserIdentity.login) == self.author.lower()
        else:
            return None
        return await session.scalar(
            select(UserIdentity.user_id)
            .where(UserIdentity.provider == "github", matches)
            .order_by(UserIdentity.last_seen_at.desc())
            .limit(1)
        )

    async def _upsert(self, session: AsyncSession, repository_id: UUID, *, overwrite: bool) -> Self:
        """Insert or update the row and return it locked for the transaction."""
        cls = type(self)
        upsert = insert(cls).values(
            id=self.id,
            repository_id=repository_id,
            number=self.number,
            owner=self.owner,
            repo=self.repo,
            **{column: getattr(self, column) for column in _GITHUB_COLUMNS},
            author_user_id=self.author_user_id,
            resolves_thread=self.resolves_thread,
        )
        github_changes = (
            {
                **{column: getattr(upsert.excluded, column) for column in _GITHUB_COLUMNS},
                "author_user_id": func.coalesce(upsert.excluded.author_user_id, cls.author_user_id),
                "resolves_thread": or_(cls.resolves_thread, upsert.excluded.resolves_thread),
            }
            if overwrite
            else {}
        )
        pull_request_id = await session.scalar(
            upsert.on_conflict_do_update(
                index_elements=[cls.repository_id, cls.number],
                set_={"updated_at": func.clock_timestamp(), **github_changes},
            ).returning(cls.id)
        )
        row = await session.scalar(
            cls._with_links(select(cls))
            .where(cls.id == pull_request_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise RuntimeError(f"pull request {self.url} vanished during save")
        return row


class PullRequestPayload(BaseModel):
    number: int | None = None
    title: str = ""
    state: str = ""
    draft: bool = False
    merged: bool = False
    author: str = Field("", validation_alias=AliasPath("user", "login"))
    author_id: int | None = Field(None, validation_alias=AliasPath("user", "id"))
    head_ref: str = Field("", validation_alias=AliasPath("head", "ref"))
    base_ref: str = Field("", validation_alias=AliasPath("base", "ref"))


class PullRequestEvent(BaseModel):
    """The slice of a GitHub ``pull_request`` webhook a PR row is built from."""

    pull_request: PullRequestPayload
    repo_full_name: str = Field("", validation_alias=AliasPath("repository", "full_name"))
    repo_private: bool | None = Field(None, validation_alias=AliasPath("repository", "private"))

    @classmethod
    def parse(cls, payload: object) -> Self | None:
        try:
            return cls.model_validate(payload)
        except ValidationError:
            return None

    @property
    def identity(self) -> tuple[str, str, int] | None:
        return pull_request_identity(
            {"repo_full_name": self.repo_full_name, "number": self.pull_request.number}
        )

    @property
    def state(self) -> PrState:
        return derive_pr_state(
            state=self.pull_request.state or None,
            merged=self.pull_request.merged,
            draft=self.pull_request.draft,
        )

    def to_pull_request(self) -> PullRequest | None:
        """An unsaved record carrying what this event says about the PR."""
        if self.identity is None:
            return None
        owner, repo, number = self.identity
        return PullRequest(
            owner=owner,
            repo=repo,
            number=number,
            state=self.state,
            title=self.pull_request.title,
            head_ref=self.pull_request.head_ref,
            base_ref=self.pull_request.base_ref,
            author=self.pull_request.author,
        )
