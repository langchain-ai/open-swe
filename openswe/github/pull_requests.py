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

import asyncio
import logging
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Literal, Self, TypedDict
from uuid import UUID, uuid7

from pydantic import AliasPath, BaseModel, Field, ValidationError
from sqlalchemy import (
    BigInteger,
    Computed,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    desc,
    func,
    inspect,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from openswe.config import ENV
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.comments import PrState, derive_pr_state
from openswe.github.org_membership import OPEN_SWE_GITHUB_LOGINS
from openswe.github.pull_request_status import pull_request_identity
from openswe.github.repositories import Repository
from openswe.review.findings import REVIEWER_THREAD_KIND
from openswe.users.models import User, UserIdentity
from openswe.utils.json_types import JsonObject, thread_metadata
from openswe.utils.langsmith import create_langsmith_thread_feedback
from openswe.utils.search import FuzzySearch
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

ThreadRole = Literal["primary", "secondary"]
AGENT_OPENED_LINK_SOURCE = "open_pull_request"

_SEARCH_PAGE_SIZE = 50
_GITHUB_COLUMNS = ("state", "title", "body", "head_ref", "base_ref", "author")
_DIFF_COLUMNS = ("additions", "deletions", "changed_files")
_WRITE_ONCE_COLUMNS = (
    "opening_base_sha",
    "opening_head_sha",
    "opening_model_id",
    "opening_effort",
    "langsmith_run_id",
    "slack_team_id",
    "slack_channel_id",
    "slack_thread_ts",
    "slack_message_ts",
)


class DiffStats(TypedDict):
    files: int
    additions: int
    deletions: int


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


class Requester(Base):
    """Someone who asked Robin for a pull request it authored as itself; never removed."""

    __tablename__ = "pull_request_requester"
    __table_args__ = (
        Index("pull_request_requester_key", "pull_request_id", text("lower(login)"), unique=True),
    )

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    # The GitHub login the review policy matches, kept even when no user resolves.
    login: Mapped[str]
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class PullRequest(Base):
    __tablename__ = "pull_request"
    __table_args__ = (
        UniqueConstraint("repository_id", "number"),
        Index("pull_request_search_idx", "search_vector", postgresql_using="gin"),
    )

    owner: Mapped[str]
    repo: Mapped[str]
    number: Mapped[int]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repository.id"), init=False)
    state: Mapped[PrState] = mapped_column(Text, default="open")
    title: Mapped[str] = mapped_column(server_default="", default="")
    body: Mapped[str] = mapped_column(server_default="", default="")
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', title), 'A') || "
            "setweight(to_tsvector('english', body), 'B')",
            persisted=True,
        ),
        init=False,
        repr=False,
    )
    head_ref: Mapped[str] = mapped_column(server_default="", default="")
    base_ref: Mapped[str] = mapped_column(server_default="", default="")
    opening_base_sha: Mapped[str] = mapped_column(server_default="", default="")
    opening_head_sha: Mapped[str] = mapped_column(server_default="", default="")
    opening_model_id: Mapped[str] = mapped_column(server_default="", default="")
    opening_effort: Mapped[str] = mapped_column(server_default="", default="")
    langsmith_run_id: Mapped[str] = mapped_column(server_default="", default="")
    slack_team_id: Mapped[str] = mapped_column(server_default="", default="")
    slack_channel_id: Mapped[str] = mapped_column(server_default="", default="")
    slack_thread_ts: Mapped[str] = mapped_column(server_default="", default="")
    slack_message_ts: Mapped[str] = mapped_column(server_default="", default="")
    author: Mapped[str] = mapped_column(server_default="", default="")
    author_github_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    # The person who asked for a pull request Open SWE authored as itself, in an Ask channel.
    author_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    resolves_thread: Mapped[bool] = mapped_column(default=False)
    # The head Robin's auto-approval last approved, which stands in for a second person.
    auto_approved_head_sha: Mapped[str] = mapped_column(server_default="", default="", init=False)
    # Opened by Robin as itself before requesters were kept, with no requester ever found.
    requester_unknown: Mapped[bool] = mapped_column(
        server_default="false", default=False, init=False
    )
    additions: Mapped[int | None] = mapped_column(default=None)
    deletions: Mapped[int | None] = mapped_column(default=None)
    changed_files: Mapped[int | None] = mapped_column(default=None)
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
    legacy_threads_discovered_at: Mapped[datetime | None] = mapped_column(init=False)
    # The task working on this pull request, and what it keeps here, one key per
    # kind of work (a baby-sit watch is ``baby_sit``).
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), default=None, init=False
    )
    task_state: Mapped[JsonObject | None] = mapped_column(JSONB, default=None, init=False)
    repository: Mapped[Repository] = relationship(init=False)

    @classmethod
    async def get(cls, owner: str, repo: str, number: int) -> Self | None:
        """The stored row, or ``None`` when nothing has saved this PR yet."""
        async with postgres.session() as session:
            return await cls(owner=owner, repo=repo, number=number).fetch(session)

    @classmethod
    async def author_user_id_of(cls, pull_request_id: UUID) -> UUID | None:
        """The Open SWE person who opened that stored PR, or ``None``."""
        async with postgres.session() as session:
            return await session.scalar(select(cls.author_user_id).where(cls.id == pull_request_id))

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

    @classmethod
    async def review_candidates(
        cls, repositories: Collection[str], user_id: UUID, *, limit: int
    ) -> list[Self]:
        """Recently updated open PRs by others that Open SWE reviewed, in ``repositories``."""
        if not repositories:
            return []
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls)
                .join(cls.repository)
                .where(
                    Repository.key.in_([name.lower() for name in repositories]),
                    cls.state == "open",
                    cls.author_user_id.is_distinct_from(user_id),
                    cls.reviews.any(),
                )
                .order_by(cls.updated_at.desc(), cls.id.desc())
                .limit(limit)
            )
            return list(rows)

    @classmethod
    async def search(
        cls, query: str, *, repositories: Sequence[str], limit: int = 50, offset: int = 0
    ) -> list[Self]:
        """Rank title/body matches within the caller's authorized repositories."""
        if not query.strip() or not repositories:
            return []
        tsquery = func.websearch_to_tsquery("english", query)
        authorized = Repository.key.in_([repository.lower() for repository in repositories])
        exact = (
            select(cls)
            .join(cls.repository)
            .where(authorized, cls.search_vector.bool_op("@@")(tsquery))
        )
        async with postgres.session() as session:
            rows = list(
                await session.scalars(
                    cls._with_links(exact)
                    .order_by(
                        func.ts_rank_cd(cls.search_vector, tsquery).desc(),
                        cls.updated_at.desc(),
                        cls.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            if rows or await session.scalar(select(exact.exists())):
                return rows
            rows = await session.scalars(
                cls._with_links(select(cls))
                .join(cls.repository)
                .where(authorized, FuzzySearch(query).condition(cls.title))
                .order_by(cls.updated_at.desc(), cls.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(rows)

    @classmethod
    async def diff_stats_for(
        cls, prs: Sequence[tuple[str, int]]
    ) -> dict[tuple[str, int], DiffStats]:
        """Stored line counts keyed by lowercased ``(repo_full_name, number)``."""
        keys = [(repo_full_name.lower(), number) for repo_full_name, number in prs]
        if not keys:
            return {}
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls).join(cls.repository).where(tuple_(Repository.key, cls.number).in_(keys))
            )
            return {
                (row.repo_full_name.lower(), row.number): stats
                for row in rows
                if (stats := row.diff_stats) is not None
            }

    @property
    def diff_stats(self) -> DiffStats | None:
        if self.additions is None or self.deletions is None or self.changed_files is None:
            return None
        return DiffStats(
            files=self.changed_files, additions=self.additions, deletions=self.deletions
        )

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def label(self) -> str:
        orgs = {org.strip().lower() for org in ENV.ALLOWED_GITHUB_ORGS.get_list()}
        repository = self.repo if self.owner.lower() in orgs else self.repo_full_name
        return f"{repository}#{self.number}"

    async def concise_walkthrough(self) -> bool:
        """Whether the owning workspace opted into terse walkthrough plans."""
        return await self.concise_for_repo(self.owner, self.repo)

    @staticmethod
    async def concise_for_repo(owner: str, repo: str) -> bool:
        """Whether the workspace owning ``owner/repo`` opted into terse walkthroughs and findings."""
        from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
        from openswe.workspaces.routing import workspace_for_repo

        workspace = await workspace_for_repo(owner, repo)
        settings = await cached_workspace_settings(workspace)
        return settings.get("concise_walkthrough_enabled") is True

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def primary_thread_id(self) -> str | None:
        return next((link.thread_id for link in self.threads if link.role == "primary"), None)

    @property
    def agent_thread_id(self) -> str | None:
        """The agent thread that created this PR; ``None`` for PRs it only linked or reused."""
        if not self.opening_head_sha:
            return None
        return next(
            (link.thread_id for link in self.threads if link.source == AGENT_OPENED_LINK_SOURCE),
            None,
        )

    async def is_authored_by(self, login: str) -> bool:
        """Whether ``login`` resolves to the same Open SWE user as this PR's author."""
        commenter = await User.for_login("github", login)
        if commenter is None:
            return False
        author_id = self.author_user_id
        if author_id is None and self.author:
            author = await User.for_login("github", self.author)
            author_id = author.id if author is not None else None
        return author_id is not None and commenter.id == author_id

    @property
    def thread_ids(self) -> list[str]:
        """Linked threads, primary first."""
        return [link.thread_id for link in self.threads if link.role == "primary"] + [
            link.thread_id for link in self.threads if link.role != "primary"
        ]

    async def save(self, *, repository_private: bool | None = None) -> Self:
        """Write the PR as GitHub describes it and register its repository.

        Overwrites the GitHub-owned columns; ``resolves_thread`` can only be set,
        never cleared. Queued ``threads``/``reviews`` are linked as well. The
        author is linked to a registered user when one matches their GitHub id
        or, failing that, their login; an unregistered author leaves it unset.
        """
        return await self._write(overwrite=True, repository_private=repository_private)

    async def ensure(self) -> Self:
        """The stored row, created bare when missing, leaving GitHub-owned columns alone."""
        return await self._write(overwrite=False)

    async def add_requesters(self, people: Mapping[str, UUID | None]) -> None:
        """Add who asked for this pull request, all or none, creating its row when missing."""
        if not people:
            return
        row = await self.ensure()
        async with postgres.session() as session:
            await session.execute(
                insert(Requester)
                .values(
                    [
                        {
                            "id": uuid7(),
                            "pull_request_id": row.id,
                            "login": login,
                            "user_id": user_id,
                            "created_by": user_id,
                            "updated_by": user_id,
                        }
                        for login, user_id in people.items()
                    ]
                )
                .on_conflict_do_nothing()
            )

    @classmethod
    async def requesters(cls, owner: str, repo: str, number: int) -> tuple[set[str], set[int]]:
        """The GitHub logins, and the numeric ids where known, of everyone who asked."""
        row = await cls.get(owner, repo, number)
        if row is None:
            return set(), set()
        async with postgres.session() as session:
            rows = (
                await session.execute(
                    select(Requester.login, Requester.user_id).where(
                        Requester.pull_request_id == row.id
                    )
                )
            ).all()
        logins = {login.lower() for login, _ in rows}
        ids: set[int] = set()
        for _, user_id in rows:
            # A renamed account is still the same person.
            if user_id is not None and (user := await User.get(user_id)):
                if user.github_login:
                    logins.add(user.github_login.lower())
                if user.github_id is not None:
                    ids.add(user.github_id)
        return logins, ids

    @classmethod
    async def record_auto_approval(cls, owner: str, repo: str, number: int, head_sha: str) -> None:
        """Remember the head Robin's auto-approval approved; ``""`` when it no longer approves."""
        row = await cls(owner=owner, repo=repo, number=number).ensure()
        async with postgres.session() as session:
            await session.execute(
                update(cls).where(cls.id == row.id).values(auto_approved_head_sha=head_sha)
            )

    @classmethod
    async def open_for_thread(cls, thread_id: str) -> list[Self]:
        """The open pull requests linked to ``thread_id``."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls)
                .join(ThreadLink)
                .where(ThreadLink.thread_id == thread_id, cls.state.in_(("open", "draft")))
            )
            return list(rows.unique())

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
        """Record review completion, optionally with a GitHub publication; deduplicate by identity."""
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

        Until the legacy ``pr_url``/``pr_urls`` thread scan has succeeded once
        for this PR, run it and link what it finds, so threads that predate the
        tables are never shadowed by a link made after them.
        """
        if not backfill or self.legacy_threads_discovered_at is not None:
            return self.thread_ids
        discovered = await self.discover_threads()
        if discovered is None:
            return self.thread_ids
        logger.info(
            "Backfilled pull request threads from legacy metadata scan",
            extra={
                "pr_repo_full_name": self.repo_full_name,
                "pr_number": self.number,
                "pr_discovered_threads": len(discovered),
            },
        )
        for thread_id in discovered:
            if all(link.thread_id != thread_id for link in self.threads):
                self.threads.append(ThreadLink(thread_id=thread_id, source="backfill"))
        return (await self._write(overwrite=False, legacy_discovered=True)).thread_ids

    async def discover_threads(self) -> Sequence[str] | None:
        """Agent threads whose metadata still points at this PR, oldest first.

        ``None`` when any search failed, so a partial result is never mistaken
        for a complete one.
        """
        client = langgraph_client()
        found: dict[str, str] = {}
        failed = False
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
                    failed = True
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
        if failed:
            return None
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
        legacy_discovered: bool = False,
    ) -> Self:
        """Persist this instance's pending (transient) links and reviews onto the stored row."""
        async with postgres.session() as session:
            repository = await Repository(
                full_name=self.repo_full_name, private=repository_private
            ).save(session)
            if overwrite and self.author_user_id is None:
                self.author_user_id = await self._author_user_id(session)
            row = await self._upsert(
                session, repository.id, overwrite=overwrite, legacy_discovered=legacy_discovered
            )
            for link in (link for link in self.threads if inspect(link).transient):
                if all(existing.thread_id != link.thread_id for existing in row.threads):
                    role: ThreadRole = (
                        "secondary" if any(t.role == "primary" for t in row.threads) else "primary"
                    )
                    row.threads.append(
                        ThreadLink(thread_id=link.thread_id, role=role, source=link.source)
                    )
            for review in (review for review in self.reviews if inspect(review).transient):
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

    async def _author_user_id(self, session: AsyncSession) -> UUID | None:
        if self.author_github_id is not None:
            matches = UserIdentity.external_id == str(self.author_github_id)
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

    async def _upsert(
        self,
        session: AsyncSession,
        repository_id: UUID,
        *,
        overwrite: bool,
        legacy_discovered: bool,
    ) -> Self:
        """Insert or update the row and return it locked for the transaction."""
        cls = type(self)
        upsert = insert(cls).values(
            id=self.id,
            repository_id=repository_id,
            number=self.number,
            owner=self.owner,
            repo=self.repo,
            **{column: getattr(self, column) for column in _GITHUB_COLUMNS},
            **{column: getattr(self, column) for column in _WRITE_ONCE_COLUMNS},
            author_github_id=self.author_github_id,
            author_user_id=self.author_user_id,
            resolves_thread=self.resolves_thread,
            **{column: getattr(self, column) for column in _DIFF_COLUMNS},
            legacy_threads_discovered_at=func.clock_timestamp() if legacy_discovered else None,
        )
        discovery_change = (
            {"legacy_threads_discovered_at": func.clock_timestamp()} if legacy_discovered else {}
        )
        github_changes = (
            {
                **{column: getattr(upsert.excluded, column) for column in _GITHUB_COLUMNS},
                **{
                    column: func.coalesce(
                        func.nullif(getattr(cls, column), ""), getattr(upsert.excluded, column)
                    )
                    for column in _WRITE_ONCE_COLUMNS
                },
                "author_github_id": func.coalesce(
                    upsert.excluded.author_github_id, cls.author_github_id
                ),
                "author_user_id": func.coalesce(upsert.excluded.author_user_id, cls.author_user_id),
                "resolves_thread": or_(cls.resolves_thread, upsert.excluded.resolves_thread),
                **{
                    column: func.coalesce(getattr(upsert.excluded, column), getattr(cls, column))
                    for column in _DIFF_COLUMNS
                },
            }
            if overwrite
            else {}
        )
        pull_request_id = await session.scalar(
            upsert.on_conflict_do_update(
                index_elements=[cls.repository_id, cls.number],
                set_={"updated_at": func.clock_timestamp(), **github_changes, **discovery_change},
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
    body: str | None = None
    state: str = ""
    draft: bool = False
    merged: bool = False
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    commits: int | None = None
    review_comments: int | None = None
    comments: int | None = None
    author: str = Field("", validation_alias=AliasPath("user", "login"))
    author_id: int | None = Field(None, validation_alias=AliasPath("user", "id"))
    head_ref: str = Field("", validation_alias=AliasPath("head", "ref"))
    head_sha: str = Field("", validation_alias=AliasPath("head", "sha"))
    base_ref: str = Field("", validation_alias=AliasPath("base", "ref"))
    base_sha: str = Field("", validation_alias=AliasPath("base", "sha"))
    merge_commit_sha: str | None = None

    @property
    def stats(self) -> dict[str, int]:
        """The size and activity counts the payload carries, by GitHub field name."""
        counts = {
            "additions": self.additions,
            "deletions": self.deletions,
            "changed_files": self.changed_files,
            "commits": self.commits,
            "review_comments": self.review_comments,
            "comments": self.comments,
        }
        return {name: count for name, count in counts.items() if count is not None}


PR_CLOSED_FEEDBACK_KEY = "pr_closed"
# Deliveries whose pull request counts are recorded as ``pr_<field>`` feedback.
_PR_STATS_ACTIONS = frozenset(["opened", "closed"])

# A pull request's ``pr_closed`` feedback value. A close without a merge names who closed it:
# the person Robin opened it for, Robin itself, someone else, or ``unknown`` when that can't be
# told. A later reopen or merge replaces it with ``reopened`` or ``merged``.
PrClosure = Literal["requester", "robin", "other", "unknown", "reopened", "merged"]


class PullRequestEvent(BaseModel):
    """The slice of a GitHub ``pull_request`` webhook a PR row is built from."""

    pull_request: PullRequestPayload
    action: str = ""
    sender_login: str = Field("", validation_alias=AliasPath("sender", "login"))
    sender_id: int | None = Field(None, validation_alias=AliasPath("sender", "id"))
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

    async def closure(self) -> PrClosure | None:
        """The ``pr_closed`` value this delivery sets; ``None`` when it leaves it alone.

        Robin is checked first, since a pull request Robin authored as itself has Robin as
        its author. Otherwise the requester is the author, or, when Robin is the author,
        anyone recorded as asking for it; with nobody recorded the closer is ``unknown``.
        """
        if self.action == "reopened":
            return "reopened"
        if self.action != "closed":
            return None
        if self.pull_request.merged:
            return "merged"
        sender = self.sender_login.lower()
        if not sender:
            return "unknown"
        if sender in OPEN_SWE_GITHUB_LOGINS:
            return "robin"
        author = self.pull_request.author.lower()
        if author and author not in OPEN_SWE_GITHUB_LOGINS:
            same_id = self.sender_id is not None and self.sender_id == self.pull_request.author_id
            return "requester" if same_id or sender == author else "other"
        if self.identity is None:
            return "unknown"
        logins, ids = await PullRequest.requesters(*self.identity)
        if not logins and not ids:
            return "unknown"
        return "requester" if sender in logins or self.sender_id in ids else "other"

    async def record_feedback(self, thread_id: str) -> None:
        """Record the pull request's counts and outcome as feedback on the thread that opened it.

        An opened or closed delivery writes each count it carries as ``pr_<field>`` with the
        count as the score. A closed or reopened delivery writes ``pr_closed`` with its
        ``closure`` as the value and no score; dashboards count closes without a merge as
        ``value in (requester, robin, other, unknown)``. Each key keeps one row per pull
        request, keyed by its URL, so a later delivery replaces the earlier value. A failure
        is logged, never raised.
        """
        pull_request = self.to_pull_request()
        if pull_request is None:
            return
        pr_url = pull_request.url
        try:
            closure = await self.closure()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not tell who closed a pull request",
                extra={"thread_id": thread_id, "pr_url": pr_url},
                exc_info=True,
            )
            closure = "unknown"
        source_info = {"source": "github_pull_request", "thread_id": thread_id, "pr_url": pr_url}
        counts = self.pull_request.stats if self.action in _PR_STATS_ACTIONS else {}
        writes = [
            create_langsmith_thread_feedback(
                thread_id,
                f"pr_{name}",
                score=float(count),
                event_id=pr_url,
                source_info=source_info,
            )
            for name, count in counts.items()
        ]
        if closure is not None:
            writes.append(
                create_langsmith_thread_feedback(
                    thread_id,
                    PR_CLOSED_FEEDBACK_KEY,
                    score=None,
                    value=closure,
                    event_id=pr_url,
                    source_info=source_info,
                )
            )
        for result in await asyncio.gather(*writes, return_exceptions=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "Failed to record pull request feedback",
                    extra={"thread_id": thread_id, "pr_url": pr_url},
                    exc_info=result,
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
            body=self.pull_request.body or "",
            head_ref=self.pull_request.head_ref,
            base_ref=self.pull_request.base_ref,
            author=self.pull_request.author,
            author_github_id=self.pull_request.author_id,
            additions=self.pull_request.additions,
            deletions=self.pull_request.deletions,
            changed_files=self.pull_request.changed_files,
        )
