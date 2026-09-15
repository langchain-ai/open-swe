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

Writers race, and GitHub webhooks arrive out of order, so every GitHub-owned
column is guarded by ``github_updated_at``: a write stamped before the stored
one leaves those columns alone. Checks and review threads hang off the PR in
their own tables, replaced wholesale by a sync and one row at a time by a
webhook.

Rows live in PostgreSQL (``POSTGRES_URI``), which this module requires. Entity
rows carry a synthetic UUIDv7 ``id``; ``(repository, number)`` stays the natural
key callers address a PR by.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self, TypedDict
from uuid import UUID, uuid7

from pydantic import AliasPath, BaseModel, BeforeValidator, Field, ValidationError
from sqlalchemy import (
    BigInteger,
    ForeignKey,
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
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, contains_eager, mapped_column, relationship, selectinload

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.comments import PrState, derive_pr_state
from agent.github.pull_request_terms import (
    FAILING_CHECK_CONCLUSIONS,
    FAILING_STATUS_STATES,
    INCONCLUSIVE_CHECK_CONCLUSIONS,
    CheckState,
    pull_request_identity,
)
from agent.github.repositories import Repository
from agent.review.findings import REVIEWER_THREAD_KIND
from agent.users.models import UserIdentity
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

ThreadRole = Literal["primary", "secondary"]
CheckKind = Literal["check_run", "status"]

# GitHub sends ``null`` rather than omitting a string it has no value for.
GithubText = Annotated[str, BeforeValidator(lambda value: value or "")]

_SEARCH_PAGE_SIZE = 50
_GITHUB_COLUMNS = (
    "state",
    "title",
    "head_ref",
    "base_ref",
    "author",
    "draft",
    "head_sha",
    "base_sha",
    "mergeable_state",
    "merged_at",
    "closed_at",
    "github_updated_at",
)


class CheckFailure(TypedDict):
    """One failing check, in the shape the dashboard PR panel renders."""

    name: str
    conclusion: str | None
    url: str | None


@dataclass(frozen=True, slots=True)
class CheckSummary:
    failing: list[CheckFailure]
    pending: int
    inconclusive: int


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


class PullRequestCheck(Base):
    """One check run or legacy commit status observed on a head sha."""

    __tablename__ = "pull_request_check"
    __table_args__ = (UniqueConstraint("pull_request_id", "head_sha", "kind", "external_id"),)

    head_sha: Mapped[str]
    kind: Mapped[CheckKind] = mapped_column(Text)
    external_id: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), init=False
    )
    name: Mapped[str] = mapped_column(server_default="", default="")
    status: Mapped[str] = mapped_column(server_default="", default="")
    conclusion: Mapped[str] = mapped_column(server_default="", default="")
    details_url: Mapped[str] = mapped_column(server_default="", default="")
    github_updated_at: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @property
    def key(self) -> tuple[str, CheckKind, str]:
        return self.head_sha, self.kind, self.external_id

    def superseded_by(self, other: PullRequestCheck) -> bool:
        """Whether ``other`` is at least as fresh; unknown timestamps always yield."""
        if self.github_updated_at is None or other.github_updated_at is None:
            return True
        return other.github_updated_at >= self.github_updated_at

    def absorb(self, other: PullRequestCheck) -> None:
        if not self.superseded_by(other):
            return
        self.name = other.name
        self.status = other.status
        self.conclusion = other.conclusion
        self.details_url = other.details_url
        self.github_updated_at = other.github_updated_at
        self.updated_at = func.clock_timestamp()

    @property
    def is_failing(self) -> bool:
        if self.kind == "status":
            return self.conclusion in FAILING_STATUS_STATES
        return self.status == "completed" and self.conclusion in FAILING_CHECK_CONCLUSIONS

    @property
    def is_pending(self) -> bool:
        if self.kind == "status":
            return self.conclusion == "pending"
        return self.status != "completed"

    @property
    def is_inconclusive(self) -> bool:
        return (
            self.kind == "check_run"
            and self.status == "completed"
            and self.conclusion in INCONCLUSIVE_CHECK_CONCLUSIONS
        )

    def as_failure(self) -> CheckFailure:
        return {
            "name": self.name,
            "conclusion": self.conclusion or None,
            "url": self.details_url or None,
        }


class PullRequestReviewThread(Base):
    """A GraphQL review thread, resolved or not, as GitHub last reported it."""

    __tablename__ = "pull_request_review_thread"
    __table_args__ = (UniqueConstraint("pull_request_id", "node_id"),)

    node_id: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), init=False
    )
    is_resolved: Mapped[bool] = mapped_column(default=False)
    path: Mapped[str] = mapped_column(server_default="", default="")
    line: Mapped[int | None] = mapped_column(default=None)
    author: Mapped[str] = mapped_column(server_default="", default="")
    body: Mapped[str] = mapped_column(server_default="", default="")
    url: Mapped[str] = mapped_column(server_default="", default="")
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    def absorb(self, other: PullRequestReviewThread) -> None:
        self.is_resolved = other.is_resolved
        self.path = other.path
        self.line = other.line
        self.author = other.author
        self.body = other.body
        self.url = other.url
        self.updated_at = func.clock_timestamp()


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
    author_github_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    author_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    draft: Mapped[bool] = mapped_column(default=False)
    head_sha: Mapped[str] = mapped_column(server_default="", default="")
    base_sha: Mapped[str] = mapped_column(server_default="", default="")
    mergeable_state: Mapped[str] = mapped_column(server_default="", default="")
    merged_at: Mapped[datetime | None] = mapped_column(default=None)
    closed_at: Mapped[datetime | None] = mapped_column(default=None)
    github_updated_at: Mapped[datetime | None] = mapped_column(default=None)
    last_synced_at: Mapped[datetime | None] = mapped_column(init=False, default=None)
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
    checks: Mapped[list[PullRequestCheck]] = relationship(
        default_factory=list,
        cascade="all, delete-orphan",
        order_by=lambda: (PullRequestCheck.name, PullRequestCheck.external_id),
    )
    review_threads: Mapped[list[PullRequestReviewThread]] = relationship(
        default_factory=list,
        cascade="all, delete-orphan",
        order_by=lambda: (PullRequestReviewThread.path, PullRequestReviewThread.node_id),
    )
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    legacy_threads_discovered_at: Mapped[datetime | None] = mapped_column(init=False)
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
    async def get_all(cls, identities: Sequence[tuple[str, str, int]]) -> list[Self]:
        """Stored rows for many ``(owner, repo, number)`` triples, in one query.

        Each row carries its repository, so a caller deciding what it may serve
        does not pay a lookup per pull request.
        """
        if not identities:
            return []
        keys = [(f"{owner}/{repo}".lower(), number) for owner, repo, number in identities]
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._with_links(select(cls))
                .join(cls.repository)
                .options(contains_eager(cls.repository))
                .where(tuple_(Repository.key, cls.number).in_(keys))
            )
            return list(rows)

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
    async def for_head_sha(cls, owner: str, repo: str, sha: str) -> list[Self]:
        """Open stored pull requests whose current head is ``sha``.

        A commit-shaped event (``check_run`` on a fork, ``status``) names no
        pull request, so its head sha is the only way back to one.
        """
        async with postgres.session() as session:
            rows = await session.scalars(
                cls._with_links(select(cls))
                .join(cls.repository)
                .where(
                    Repository.key == f"{owner}/{repo}".lower(),
                    cls.head_sha == sha,
                    cls.state.in_(("open", "draft")),
                )
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

    @property
    def unresolved_review_threads(self) -> list[PullRequestReviewThread]:
        return [thread for thread in self.review_threads if not thread.is_resolved]

    @property
    def head_checks(self) -> list[PullRequestCheck]:
        """Stored checks for the current head sha, ignoring any older leftovers."""
        if not self.head_sha:
            return list(self.checks)
        return [check for check in self.checks if check.head_sha == self.head_sha]

    def check_summary(self) -> CheckSummary:
        """Failing checks plus pending and inconclusive counts for the current head."""
        checks = self.head_checks
        return CheckSummary(
            failing=[check.as_failure() for check in checks if check.is_failing],
            pending=sum(1 for check in checks if check.is_pending),
            inconclusive=sum(1 for check in checks if check.is_inconclusive),
        )

    def synced_within(self, max_age: timedelta) -> bool:
        """Whether this row is recent enough to answer for GitHub.

        A row no full sync has ever seen never qualifies, however many webhooks
        have touched it: its check and review-thread sets were never populated.
        Past that, the newer of the sync and GitHub's own stamp measures age, so
        a webhook that just described a change keeps the row serving.
        """
        if self.last_synced_at is None:
            return False
        seen = max(self.last_synced_at, self.github_updated_at or self.last_synced_at)
        return datetime.now(UTC) - seen <= max_age

    @property
    def check_state(self) -> CheckState:
        """The sidebar dot: ``unknown`` until a full sync has seen this PR."""
        if self.last_synced_at is None:
            return "unknown"
        summary = self.check_summary()
        if summary.failing:
            return "failing"
        return "pending" if summary.pending else "passing"

    async def save(self, *, repository_private: bool | None = None, synced: bool = False) -> Self:
        """Write the PR as GitHub describes it and register its repository.

        Overwrites the GitHub-owned columns unless this instance carries a
        ``github_updated_at`` older than the stored one, in which case they are
        left alone; ``resolves_thread`` can only be set, never cleared. Queued
        ``threads``/``reviews`` are linked as well. The author is linked to a
        registered user when one matches their GitHub id or, failing that, their
        login; an unregistered author leaves it unset. ``synced`` stamps
        ``last_synced_at``, claiming this write came from a full GitHub read.
        """
        return await self._write(
            overwrite=True, repository_private=repository_private, synced=synced
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

    async def replace_checks(self, head_sha: str, checks: Sequence[PullRequestCheck]) -> Self:
        """Make ``checks`` the whole check set for ``head_sha``, dropping every other sha.

        Rows already stored for ``head_sha`` are updated in place (keeping their
        id) unless the stored row is the fresher of the two.
        """
        async with postgres.session() as session:
            row = await self._stored(session)
            incoming = {(check.kind, check.external_id): check for check in checks}
            kept: list[PullRequestCheck] = []
            for existing in row.checks:
                if existing.head_sha != head_sha:
                    continue
                replacement = incoming.pop((existing.kind, existing.external_id), None)
                if replacement is None:
                    continue
                existing.absorb(replacement)
                kept.append(existing)
            for check in incoming.values():
                check.head_sha = head_sha
                kept.append(check)
            row.checks[:] = kept
            await session.flush()
            return await self._reread(session)

    async def upsert_check(self, check: PullRequestCheck) -> Self:
        """Record one check from a ``check_run``/``status`` webhook, newest wins."""
        async with postgres.session() as session:
            row = await self._stored(session)
            existing = next((stored for stored in row.checks if stored.key == check.key), None)
            if existing is None:
                row.checks.append(check)
            else:
                existing.absorb(check)
            await session.flush()
            return await self._reread(session)

    async def replace_review_threads(self, threads: Sequence[PullRequestReviewThread]) -> Self:
        """Make ``threads`` the whole review-thread set, keyed by GraphQL node id."""
        async with postgres.session() as session:
            row = await self._stored(session)
            incoming = {thread.node_id: thread for thread in threads}
            kept: list[PullRequestReviewThread] = []
            for existing in row.review_threads:
                replacement = incoming.pop(existing.node_id, None)
                if replacement is None:
                    continue
                existing.absorb(replacement)
                kept.append(existing)
            kept.extend(incoming.values())
            row.review_threads[:] = kept
            await session.flush()
            return await self._reread(session)

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

    async def primary_thread(self, *, backfill: bool = True) -> str | None:
        threads = await self.linked_threads(backfill=backfill)
        return threads[0] if threads else None

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

    async def _stored(self, session: AsyncSession) -> Self:
        row = await self.fetch(session)
        if row is None:
            raise RuntimeError(f"pull request {self.url} has no stored row; save it first")
        return row

    async def _reread(self, session: AsyncSession) -> Self:
        row = await self.fetch(session)
        if row is None:
            raise RuntimeError(f"pull request {self.url} vanished during save")
        return row

    @classmethod
    def _with_links(cls, statement):  # noqa: ANN001, ANN206
        return statement.options(
            selectinload(cls.threads),
            selectinload(cls.reviews),
            selectinload(cls.checks),
            selectinload(cls.review_threads),
        )

    async def _write(
        self,
        *,
        overwrite: bool,
        repository_private: bool | None = None,
        legacy_discovered: bool = False,
        synced: bool = False,
    ) -> Self:
        """Persist this instance's pending (transient) links and reviews onto the stored row."""
        async with postgres.session() as session:
            repository = await Repository(
                full_name=self.repo_full_name, private=repository_private
            ).save(session)
            if overwrite and self.author_user_id is None:
                self.author_user_id = await self._author_user_id(session)
            row = await self._upsert(
                session,
                repository.id,
                overwrite=overwrite,
                legacy_discovered=legacy_discovered,
                synced=synced,
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
        synced: bool = False,
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
            author_github_id=self.author_github_id,
            author_user_id=self.author_user_id,
            resolves_thread=self.resolves_thread,
            legacy_threads_discovered_at=func.clock_timestamp() if legacy_discovered else None,
            last_synced_at=func.clock_timestamp() if synced else None,
        )
        discovery_change = (
            {"legacy_threads_discovered_at": func.clock_timestamp()} if legacy_discovered else {}
        )
        sync_change = {"last_synced_at": func.clock_timestamp()} if synced else {}
        # An event GitHub stamped before the one already stored describes an older
        # world, so its GitHub-owned values are dropped rather than written back.
        stale = and_(
            cls.github_updated_at.is_not(None),
            upsert.excluded.github_updated_at.is_not(None),
            upsert.excluded.github_updated_at < cls.github_updated_at,
        )

        def newest(stored, incoming):  # noqa: ANN001, ANN202
            return case((stale, stored), else_=incoming)

        github_changes = (
            {
                **{
                    column: newest(getattr(cls, column), getattr(upsert.excluded, column))
                    for column in _GITHUB_COLUMNS
                },
                "author_github_id": newest(
                    cls.author_github_id,
                    func.coalesce(upsert.excluded.author_github_id, cls.author_github_id),
                ),
                "author_user_id": newest(
                    cls.author_user_id,
                    func.coalesce(upsert.excluded.author_user_id, cls.author_user_id),
                ),
                "github_updated_at": newest(
                    cls.github_updated_at,
                    func.coalesce(upsert.excluded.github_updated_at, cls.github_updated_at),
                ),
                "resolves_thread": or_(cls.resolves_thread, upsert.excluded.resolves_thread),
            }
            if overwrite
            else {}
        )
        pull_request_id = await session.scalar(
            upsert.on_conflict_do_update(
                index_elements=[cls.repository_id, cls.number],
                set_={
                    "updated_at": func.clock_timestamp(),
                    **github_changes,
                    **discovery_change,
                    **sync_change,
                },
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
    """A GitHub pull request object, as both the REST API and webhooks send it."""

    number: int | None = None
    title: GithubText = ""
    state: GithubText = ""
    draft: bool = False
    merged: bool = False
    mergeable_state: GithubText = ""
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    updated_at: datetime | None = None
    author: GithubText = Field("", validation_alias=AliasPath("user", "login"))
    author_id: int | None = Field(None, validation_alias=AliasPath("user", "id"))
    head_ref: GithubText = Field("", validation_alias=AliasPath("head", "ref"))
    head_sha: GithubText = Field("", validation_alias=AliasPath("head", "sha"))
    base_ref: GithubText = Field("", validation_alias=AliasPath("base", "ref"))
    base_sha: GithubText = Field("", validation_alias=AliasPath("base", "sha"))
    base_repo_private: bool | None = Field(
        None, validation_alias=AliasPath("base", "repo", "private")
    )

    @property
    def pr_state(self) -> PrState:
        return derive_pr_state(state=self.state or None, merged=self.merged, draft=self.draft)

    def to_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        """An unsaved record carrying what this payload says about the PR."""
        return PullRequest(
            owner=owner,
            repo=repo,
            number=number,
            state=self.pr_state,
            title=self.title,
            head_ref=self.head_ref,
            base_ref=self.base_ref,
            author=self.author,
            author_github_id=self.author_id,
            draft=self.draft,
            head_sha=self.head_sha,
            base_sha=self.base_sha,
            mergeable_state=self.mergeable_state,
            merged_at=self.merged_at,
            closed_at=self.closed_at,
            github_updated_at=self.updated_at,
        )


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
        return self.pull_request.pr_state

    def to_pull_request(self) -> PullRequest | None:
        """An unsaved record carrying what this event says about the PR."""
        if self.identity is None:
            return None
        return self.pull_request.to_pull_request(*self.identity)
