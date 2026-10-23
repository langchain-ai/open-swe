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
from typing import Literal, Self, TypedDict
from uuid import UUID, uuid7

from pydantic import AliasPath, BaseModel, Field, ValidationError
from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Computed,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    and_,
    case,
    desc,
    func,
    inspect,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload
from sqlalchemy.sql.base import ColumnCollection

from openswe.config import ENV
from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.comments import PrState, derive_pr_state
from openswe.github.pull_request_status import pull_request_identity
from openswe.github.repositories import Repository
from openswe.review.findings import REVIEWER_THREAD_KIND
from openswe.users.models import User, UserIdentity
from openswe.utils.json_types import JsonObject, thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

ThreadRole = Literal["primary", "secondary"]
AGENT_OPENED_LINK_SOURCE = "open_pull_request"

_SEARCH_PAGE_SIZE = 50
# What GitHub says of the PR, overwritten whole by any newer description of it.
_GITHUB_COLUMNS = (
    "state",
    "title",
    "body",
    "head_ref",
    "base_ref",
    "author",
    "node_id",
    "head_sha",
    "base_sha",
    "github_created_at",
    "merged_at",
    "closed_at",
)
# GitHub columns some descriptions leave out (a webhook's abridged PR, a listing):
# ``None`` keeps what is stored.
_PARTIAL_COLUMNS = ("additions", "deletions", "changed_files", "github_id", "commit_count")
_LIST_COLUMNS = ("labels", "assignees", "requested_reviewers", "requested_teams")
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


class Label(TypedDict):
    name: str
    color: str
    description: str


class GitHubAccount(TypedDict):
    """A GitHub user as a pull request names one; requests are matched by ``id``."""

    id: int | None
    login: str


class GitHubTeam(TypedDict):
    id: int | None
    slug: str


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
    author_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    resolves_thread: Mapped[bool] = mapped_column(default=False)
    additions: Mapped[int | None] = mapped_column(default=None)
    deletions: Mapped[int | None] = mapped_column(default=None)
    changed_files: Mapped[int | None] = mapped_column(default=None)
    github_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    node_id: Mapped[str] = mapped_column(server_default="", default="")
    head_sha: Mapped[str] = mapped_column(server_default="", default="")
    base_sha: Mapped[str] = mapped_column(server_default="", default="")
    # ``None`` while GitHub is still computing it, or until something has read it.
    mergeable: Mapped[bool | None] = mapped_column(default=None)
    mergeable_state: Mapped[str] = mapped_column(server_default="", default="")
    # GraphQL's ``reviewDecision`` lowercased, ``none`` when branch protection
    # asks for no review; ``""`` until a refresh has read it.
    review_decision: Mapped[str] = mapped_column(server_default="", default="")
    commit_count: Mapped[int | None] = mapped_column(default=None)
    # ``None`` on an unsaved record: the description it came from left them out.
    labels: Mapped[list[Label] | None] = mapped_column(JSONB, default=None)
    assignees: Mapped[list[GitHubAccount] | None] = mapped_column(JSONB, default=None)
    requested_reviewers: Mapped[list[GitHubAccount] | None] = mapped_column(JSONB, default=None)
    requested_teams: Mapped[list[GitHubTeam] | None] = mapped_column(JSONB, default=None)
    github_created_at: Mapped[datetime | None] = mapped_column(default=None)
    github_updated_at: Mapped[datetime | None] = mapped_column(default=None)
    merged_at: Mapped[datetime | None] = mapped_column(default=None)
    closed_at: Mapped[datetime | None] = mapped_column(default=None)
    synced_at: Mapped[datetime | None] = mapped_column(default=None, init=False)
    synced_head_sha: Mapped[str] = mapped_column(server_default="", default="", init=False)
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
    async def search(
        cls, query: str, *, repositories: Sequence[str], limit: int = 50, offset: int = 0
    ) -> list[Self]:
        """Rank title/body matches within the caller's authorized repositories."""
        if not query.strip() or not repositories:
            return []
        tsquery = func.websearch_to_tsquery("english", query)
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._with_links(select(cls))
                .join(cls.repository)
                .where(
                    Repository.key.in_([repository.lower() for repository in repositories]),
                    cls.search_vector.bool_op("@@")(tsquery),
                )
                .order_by(
                    func.ts_rank_cd(cls.search_vector, tsquery).desc(),
                    cls.updated_at.desc(),
                    cls.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            return list(rows)

    @classmethod
    async def many(cls, prs: Sequence[tuple[str, int]]) -> dict[tuple[str, int], Self]:
        """Stored rows, without their links, keyed by lowercased ``(repo_full_name, number)``."""
        keys = list({(repo_full_name.lower(), number) for repo_full_name, number in prs})
        if not keys or not postgres.configured():
            return {}
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls).join(cls.repository).where(tuple_(Repository.key, cls.number).in_(keys))
            )
            return {(row.repo_full_name.lower(), row.number): row for row in rows}

    @classmethod
    async def diff_stats_for(
        cls, prs: Sequence[tuple[str, int]]
    ) -> dict[tuple[str, int], DiffStats]:
        """Stored line counts keyed by lowercased ``(repo_full_name, number)``."""
        return {
            key: stats
            for key, row in (await cls.many(prs)).items()
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

        Overwrites the GitHub-owned columns unless the stored description is
        newer by ``github_updated_at``; a record without one only fills a row no
        GitHub description has reached yet. ``None`` columns keep what is stored.
        ``resolves_thread`` can only be set, never cleared. Queued ``threads``/``reviews`` are linked as well. The
        author is linked to a registered user when one matches their GitHub id
        or, failing that, their login; an unregistered author leaves it unset.
        """
        return await self._write(overwrite=True, repository_private=repository_private)

    async def ensure(self) -> Self:
        """The stored row, created bare when missing, leaving GitHub-owned columns alone."""
        return await self._write(overwrite=False)

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

    def _github_changes(self, excluded: ColumnCollection) -> dict[str, object]:
        """The ``ON CONFLICT`` assignments that write what this record says GitHub holds."""
        cls = type(self)
        # A delivery can arrive after a newer one; the older description loses.
        fresh = or_(
            cls.github_updated_at.is_(None),
            excluded.github_updated_at >= cls.github_updated_at,
        )

        def newest(column: str, value: ColumnElement[object]) -> ColumnElement[object]:
            return case((fresh, value), else_=getattr(cls, column))

        def kept(column: str) -> ColumnElement[object]:
            return func.coalesce(getattr(excluded, column), getattr(cls, column))

        # GitHub recomputes mergeability for every new head and answers null meanwhile.
        moved = and_(excluded.head_sha != "", excluded.head_sha != cls.head_sha)
        return {
            **{column: newest(column, getattr(excluded, column)) for column in _GITHUB_COLUMNS},
            **{
                column: func.coalesce(
                    func.nullif(getattr(cls, column), ""), getattr(excluded, column)
                )
                for column in _WRITE_ONCE_COLUMNS
            },
            "author_github_id": kept("author_github_id"),
            "author_user_id": kept("author_user_id"),
            "resolves_thread": or_(cls.resolves_thread, excluded.resolves_thread),
            **{column: newest(column, kept(column)) for column in _PARTIAL_COLUMNS},
            **{
                column: newest(column, getattr(excluded, column))
                for column in _LIST_COLUMNS
                if getattr(self, column) is not None
            },
            "mergeable": newest(
                "mergeable", case((moved, excluded.mergeable), else_=kept("mergeable"))
            ),
            "mergeable_state": newest(
                "mergeable_state",
                case(
                    (moved, excluded.mergeable_state),
                    else_=func.coalesce(
                        func.nullif(excluded.mergeable_state, ""), cls.mergeable_state
                    ),
                ),
            ),
            **({"review_decision": excluded.review_decision} if self.review_decision else {}),
            "github_updated_at": func.greatest(cls.github_updated_at, excluded.github_updated_at),
        }

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
            **{column: getattr(self, column) for column in _PARTIAL_COLUMNS},
            **{column: getattr(self, column) or [] for column in _LIST_COLUMNS},
            mergeable=self.mergeable,
            mergeable_state=self.mergeable_state,
            review_decision=self.review_decision,
            github_updated_at=self.github_updated_at,
            legacy_threads_discovered_at=func.clock_timestamp() if legacy_discovered else None,
        )
        discovery_change = (
            {"legacy_threads_discovered_at": func.clock_timestamp()} if legacy_discovered else {}
        )
        github_changes = self._github_changes(upsert.excluded) if overwrite else {}
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


class _Account(BaseModel):
    id: int | None = None
    login: str = ""

    def as_account(self) -> GitHubAccount:
        return GitHubAccount(id=self.id, login=self.login)


class _Team(BaseModel):
    id: int | None = None
    slug: str = ""


class _Label(BaseModel):
    name: str
    color: str = ""
    description: str | None = None


class PullRequestPayload(BaseModel):
    """A pull request as GitHub's REST API and webhooks describe it.

    Abridged descriptions (a review webhook's, a listing's) leave out some
    fields; those stay ``None`` and keep what is stored.
    """

    number: int | None = None
    id: int | None = None
    node_id: str = ""
    title: str = ""
    body: str | None = None
    state: str = ""
    draft: bool = False
    merged: bool = False
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    commits: int | None = None
    mergeable: bool | None = None
    mergeable_state: str | None = None
    author: str = Field("", validation_alias=AliasPath("user", "login"))
    author_id: int | None = Field(None, validation_alias=AliasPath("user", "id"))
    head_ref: str = Field("", validation_alias=AliasPath("head", "ref"))
    head_sha: str = Field("", validation_alias=AliasPath("head", "sha"))
    base_ref: str = Field("", validation_alias=AliasPath("base", "ref"))
    base_sha: str = Field("", validation_alias=AliasPath("base", "sha"))
    merge_commit_sha: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    labels: list[_Label] | None = None
    assignees: list[_Account] | None = None
    requested_reviewers: list[_Account] | None = None
    requested_teams: list[_Team] | None = None

    @property
    def pr_state(self) -> PrState:
        return derive_pr_state(
            state=self.state or None,
            merged=self.merged or self.merged_at is not None,
            draft=self.draft,
        )

    def to_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        """An unsaved record carrying what this description says about the PR."""
        return PullRequest(
            owner=owner,
            repo=repo,
            number=number,
            github_id=self.id,
            node_id=self.node_id,
            state=self.pr_state,
            title=self.title,
            body=self.body or "",
            head_ref=self.head_ref,
            head_sha=self.head_sha,
            base_ref=self.base_ref,
            base_sha=self.base_sha,
            author=self.author,
            author_github_id=self.author_id,
            additions=self.additions,
            deletions=self.deletions,
            changed_files=self.changed_files,
            commit_count=self.commits,
            mergeable=self.mergeable,
            mergeable_state=(self.mergeable_state or "").lower(),
            github_created_at=self.created_at,
            github_updated_at=self.updated_at,
            merged_at=self.merged_at,
            closed_at=self.closed_at,
            labels=(
                None
                if self.labels is None
                else [
                    Label(name=label.name, color=label.color, description=label.description or "")
                    for label in self.labels
                ]
            ),
            assignees=(
                None if self.assignees is None else [user.as_account() for user in self.assignees]
            ),
            requested_reviewers=(
                None
                if self.requested_reviewers is None
                else [user.as_account() for user in self.requested_reviewers]
            ),
            requested_teams=(
                None
                if self.requested_teams is None
                else [GitHubTeam(id=team.id, slug=team.slug) for team in self.requested_teams]
            ),
        )


class PullRequestEvent(BaseModel):
    """The slice of a GitHub webhook naming a pull request that a PR row is built from."""

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
        return self.pull_request.pr_state

    def to_pull_request(self) -> PullRequest | None:
        """An unsaved record carrying what this event says about the PR."""
        if self.identity is None:
            return None
        return self.pull_request.to_pull_request(*self.identity)
