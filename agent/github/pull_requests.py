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

The row also mirrors GitHub, so pages read a PR without calling GitHub: a save
carrying GitHub's ``updated_at`` is a snapshot and writes the head/base SHAs,
people and labels too, unless the stored snapshot is newer. Webhooks keep it
current; ``mirrored`` fetches a PR nobody has mirrored yet and refreshes a stale
one in the background. ``sync_revision`` lists the files of the current head
and its check runs, which webhooks alone cannot supply.
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal, Self, TypedDict
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid7

import httpx2
from fastapi import HTTPException
from pydantic import AliasPath, BaseModel, Field, ValidationError
from sqlalchemy import (
    BigInteger,
    Computed,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    case,
    delete,
    desc,
    exists,
    func,
    inspect,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.app import get_github_app_installation_token
from agent.github.check_runs import CheckRun, CheckRunEvent
from agent.github.comments import PrState, derive_pr_state
from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.github.pull_request_diff import (
    GITHUB_MAX_LISTED_FILES,
    list_pull_request_files,
    merge_base_sha,
)
from agent.github.pull_request_status import pull_request_identity
from agent.github.repositories import Repository
from agent.review.findings import REVIEWER_THREAD_KIND
from agent.ui_invalidations import Topic
from agent.users.models import User, UserIdentity
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

ThreadRole = Literal["primary", "secondary"]
FileStatus = Literal["added", "removed", "modified", "renamed", "copied", "changed", "unchanged"]
AGENT_OPENED_LINK_SOURCE = "open_pull_request"

STALE_AFTER = timedelta(seconds=30)
"""How old a mirrored row may be before a read refreshes it in the background."""

_SEARCH_PAGE_SIZE = 50
_GITHUB_COLUMNS = ("state", "title", "body", "head_ref", "base_ref", "author")
_SNAPSHOT_COLUMNS = (
    "head_sha",
    "base_sha",
    "commits",
    "author_avatar_url",
    "labels",
    "assignees",
    "requested_reviewers",
    "github_updated_at",
    "github_created_at",
    "merged_at",
)
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


class UserRef(TypedDict):
    login: str
    avatar_url: str | None


class LabelRef(TypedDict):
    name: str
    color: str | None


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
    head_sha: Mapped[str] = mapped_column(server_default="", default="")
    base_sha: Mapped[str] = mapped_column(server_default="", default="")
    commits: Mapped[int | None] = mapped_column(default=None)
    author_avatar_url: Mapped[str] = mapped_column(server_default="", default="")
    labels: Mapped[list[LabelRef]] = mapped_column(JSONB, default_factory=list)
    assignees: Mapped[list[UserRef]] = mapped_column(JSONB, default_factory=list)
    requested_reviewers: Mapped[list[UserRef]] = mapped_column(JSONB, default_factory=list)
    github_updated_at: Mapped[datetime | None] = mapped_column(default=None)
    github_created_at: Mapped[datetime | None] = mapped_column(default=None)
    merged_at: Mapped[datetime | None] = mapped_column(default=None)
    synced_at: Mapped[datetime | None] = mapped_column(default=None, init=False)
    files_head_sha: Mapped[str] = mapped_column(server_default="", default="", init=False)
    files_base_ref: Mapped[str] = mapped_column(server_default="", default="", init=False)
    merge_base_sha: Mapped[str] = mapped_column(server_default="", default="", init=False)
    files_truncated: Mapped[bool] = mapped_column(default=False, init=False)
    checks_head_sha: Mapped[str] = mapped_column(server_default="", default="", init=False)
    checks_synced_at: Mapped[datetime | None] = mapped_column(default=None, init=False)
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
    async def diff_stats_for(
        cls, prs: Sequence[tuple[str, int]]
    ) -> dict[tuple[str, int], DiffStats]:
        """Stored line counts keyed by lowercased ``(repo_full_name, number)``."""
        keys = [(repo_full_name.lower(), number) for repo_full_name, number in prs]
        if not keys or not postgres.configured():
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

    @property
    def is_snapshot(self) -> bool:
        """Whether this record carries a whole GitHub PR, so saving it refreshes the mirror."""
        return self.github_updated_at is not None and bool(self.head_sha)

    @staticmethod
    def topic_key_of(owner: str, repo: str, number: int) -> str:
        """The PR's key in ``Topic.PULL_REQUESTS``; GitHub names are case-insensitive."""
        return f"{owner.lower()}/{repo.lower()}/{number}"

    @property
    def topic_key(self) -> str:
        return self.topic_key_of(self.owner, self.repo, self.number)

    @property
    def files_current(self) -> bool:
        return bool(self.head_sha) and (self.files_head_sha, self.files_base_ref) == (
            self.head_sha,
            self.base_ref,
        )

    @property
    def checks_current(self) -> bool:
        return bool(self.head_sha) and self.checks_head_sha == self.head_sha

    @property
    def snapshot_stale(self) -> bool:
        return self.synced_at is None or datetime.now(UTC) - self.synced_at >= STALE_AFTER

    @classmethod
    async def mirrored(cls, owner: str, repo: str, number: int) -> PullRequest:
        """The mirrored PR, fetched first when never mirrored and refreshed in the background when stale."""
        row = await cls.get(owner, repo, number)
        if row is None or row.synced_at is None:
            row = await cls.pull(owner, repo, number)
        if row.snapshot_stale or not (row.files_current and row.checks_current):
            cls.refresh_in_background(owner, repo, number)
        return row

    @classmethod
    async def pull(cls, owner: str, repo: str, number: int) -> PullRequest:
        """Fetch the PR from GitHub into the mirror; concurrent callers share one fetch."""
        key = (owner.lower(), repo.lower(), number)
        if key not in _PULLS:
            _PULLS[key] = asyncio.ensure_future(cls._pull(owner, repo, number))
            _PULLS[key].add_done_callback(lambda _done: _PULLS.pop(key, None))
        return await asyncio.shield(_PULLS[key])

    @classmethod
    async def _pull(cls, owner: str, repo: str, number: int) -> PullRequest:
        async with github_client(token=await cls._github_token()) as client:
            payload = await PullRequestPayload.fetch(client, owner, repo, number)
        return await payload.to_pull_request(owner, repo, number).save(
            repository_private=payload.repo_private
        )

    @classmethod
    def refresh_in_background(cls, owner: str, repo: str, number: int) -> None:
        key = (owner.lower(), repo.lower(), number)
        if key in _REFRESHES:
            return
        _REFRESHES[key] = asyncio.create_task(
            cls._refresh(owner, repo, number), name="pull-request-refresh"
        )
        _REFRESHES[key].add_done_callback(lambda _done: _REFRESHES.pop(key, None))

    @classmethod
    async def _refresh(cls, owner: str, repo: str, number: int) -> None:
        try:
            row = await cls.get(owner, repo, number)
            if row is not None and not row.snapshot_stale:
                await row.sync_revision()
                return
            before = row.github_updated_at if row is not None else None
            row = await cls.pull(owner, repo, number)
            if row.github_updated_at != before:
                await Topic.PULL_REQUESTS.invalidate(key=row.topic_key)
            await row.sync_revision(recheck_unfinished=True)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Refreshing a mirrored pull request failed",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
                exc_info=True,
            )

    async def changed_on_github(self) -> None:
        """Tell open pages this saved snapshot changed, and list a new head's files and checks."""
        await Topic.PULL_REQUESTS.invalidate(key=self.topic_key)
        if not (self.files_current and self.checks_current):
            type(self).refresh_in_background(self.owner, self.repo, self.number)

    async def sync_revision(self, *, recheck_unfinished: bool = False) -> None:
        """List the current head's files and check runs when the mirror lacks them.

        ``recheck_unfinished`` lists check runs again while any stored one is
        unfinished, covering a ``check_run`` webhook GitHub never delivered.
        """
        relist_checks = not self.checks_current or (
            recheck_unfinished
            and any(run.status != "completed" for run in await self.check_runs() or [])
        )
        if self.files_current and not relist_checks:
            return
        async with github_client(token=await self._github_token()) as client:
            if not self.files_current:
                await self._sync_files(client)
            if relist_checks:
                await self._sync_checks(client)

    async def _sync_files(self, client: httpx2.AsyncClient) -> None:
        head_sha, base_ref = self.head_sha, self.base_ref
        raw = await list_pull_request_files(client, self.repo_full_name, self.number)
        files = [PullRequestFilePayload.model_validate(item) for item in raw]
        if PullRequestFilePayload.listing_moved(files, head_sha):
            logger.info(
                "Pull request head moved while listing its files",
                extra={"pr_repo_full_name": self.repo_full_name, "pr_number": self.number},
            )
            return
        merge_base = await merge_base_sha(client, self.repo_full_name, self.base_sha, head_sha)
        cls = type(self)
        async with postgres.session() as session:
            current = (
                await session.execute(
                    select(cls.head_sha, cls.base_ref).where(cls.id == self.id).with_for_update()
                )
            ).one_or_none()
            if current is None or (current.head_sha, current.base_ref) != (head_sha, base_ref):
                return
            await session.execute(
                delete(PullRequestFile).where(PullRequestFile.pull_request_id == self.id)
            )
            session.add_all(
                PullRequestFile(
                    pull_request_id=self.id,
                    position=position,
                    path=file.filename,
                    status=file.status,
                    previous_path=file.previous_filename,
                    additions=file.additions,
                    deletions=file.deletions,
                )
                for position, file in enumerate(files)
            )
            await session.execute(
                update(cls)
                .where(cls.id == self.id)
                .values(
                    files_head_sha=head_sha,
                    files_base_ref=base_ref,
                    merge_base_sha=merge_base,
                    files_truncated=len(raw) >= GITHUB_MAX_LISTED_FILES,
                )
            )
            await Topic.PULL_REQUESTS.invalidate(session, key=self.topic_key)
        self.files_head_sha, self.files_base_ref, self.merge_base_sha = (
            head_sha,
            base_ref,
            merge_base,
        )

    async def _sync_checks(self, client: httpx2.AsyncClient) -> None:
        head_sha = self.head_sha
        runs = await CheckRun.fetch_for_commit(client, self.repo_full_name, head_sha)
        cls = type(self)
        async with postgres.session() as session:
            current = (
                await session.execute(
                    select(cls.head_sha, cls.checks_head_sha)
                    .where(cls.id == self.id)
                    .with_for_update()
                )
            ).one_or_none()
            if current is None or current.head_sha != head_sha:
                return
            await CheckRun.store(
                session,
                [
                    CheckRun.from_payload(run, self.repository_id)
                    for run in runs
                    if run.head_sha == head_sha
                ],
            )
            await session.execute(
                update(cls)
                .where(cls.id == self.id)
                .values(checks_head_sha=head_sha, checks_synced_at=func.clock_timestamp())
            )
            previous = current.checks_head_sha
            if previous and previous != head_sha:
                still_a_head = await session.scalar(
                    select(
                        exists().where(
                            cls.repository_id == self.repository_id, cls.head_sha == previous
                        )
                    )
                )
                if not still_a_head:
                    await CheckRun.forget_commit(session, self.repository_id, previous)
            await Topic.PULL_REQUESTS.invalidate(session, key=self.topic_key)
        self.checks_head_sha = head_sha

    async def files(self) -> list[PullRequestFile]:
        """The current head's files in GitHub's order; meaningful only when ``files_current``."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(PullRequestFile)
                .where(PullRequestFile.pull_request_id == self.id)
                .order_by(PullRequestFile.position)
            )
            return list(rows)

    async def check_runs(self) -> list[CheckRun] | None:
        """The head's check runs; ``None`` while none are known and the head is not listed yet."""
        runs = await CheckRun.for_commit(self.repository_id, self.head_sha) if self.head_sha else []
        return runs if runs or self.checks_current else None

    @classmethod
    async def record_check_run(cls, payload: object) -> None:
        """Mirror a ``check_run`` webhook onto the PRs whose head it ran on."""
        try:
            event = CheckRunEvent.model_validate(payload)
        except ValidationError:
            logger.info("Ignoring an unreadable check_run webhook", exc_info=True)
            return
        run = event.check_run
        async with postgres.session() as session:
            heads = list(
                await session.execute(
                    select(cls.repository_id, cls.owner, cls.repo, cls.number)
                    .join(Repository, Repository.id == cls.repository_id)
                    .where(
                        Repository.key == event.repo_full_name.lower(),
                        cls.head_sha == run.head_sha,
                    )
                )
            )
            if not heads:
                return
            await CheckRun.store(session, [CheckRun.from_payload(run, heads[0].repository_id)])
            for head in heads:
                await Topic.PULL_REQUESTS.invalidate(
                    session, key=cls.topic_key_of(head.owner, head.repo, head.number)
                )

    @staticmethod
    async def _github_token() -> str:
        token = await get_github_app_installation_token()
        if not token:
            raise HTTPException(503, "GitHub App token unavailable")
        return token

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
            **{column: getattr(self, column) for column in _SNAPSHOT_COLUMNS},
            **{column: getattr(self, column) for column in _WRITE_ONCE_COLUMNS},
            author_github_id=self.author_github_id,
            author_user_id=self.author_user_id,
            resolves_thread=self.resolves_thread,
            **{column: getattr(self, column) for column in _DIFF_COLUMNS},
            synced_at=func.clock_timestamp() if self.is_snapshot else None,
            legacy_threads_discovered_at=func.clock_timestamp() if legacy_discovered else None,
        )
        discovery_change = (
            {"legacy_threads_discovered_at": func.clock_timestamp()} if legacy_discovered else {}
        )
        if self.is_snapshot:
            newer = or_(
                cls.github_updated_at.is_(None),
                upsert.excluded.github_updated_at >= cls.github_updated_at,
            )
            github_owned = {
                **{
                    column: case(
                        (newer, getattr(upsert.excluded, column)), else_=getattr(cls, column)
                    )
                    for column in (*_GITHUB_COLUMNS, *_SNAPSHOT_COLUMNS)
                },
                **{
                    column: case(
                        (
                            newer,
                            func.coalesce(getattr(upsert.excluded, column), getattr(cls, column)),
                        ),
                        else_=getattr(cls, column),
                    )
                    for column in _DIFF_COLUMNS
                },
                "synced_at": case((newer, func.clock_timestamp()), else_=cls.synced_at),
            }
        else:
            github_owned = {
                **{column: getattr(upsert.excluded, column) for column in _GITHUB_COLUMNS},
                **{
                    column: func.coalesce(getattr(upsert.excluded, column), getattr(cls, column))
                    for column in _DIFF_COLUMNS
                },
            }
        github_changes = (
            {
                **github_owned,
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


class _UserPayload(BaseModel):
    login: str = ""
    avatar_url: str | None = None

    @classmethod
    def refs(cls, users: Sequence[Self]) -> list[UserRef]:
        return [
            UserRef(login=user.login, avatar_url=user.avatar_url) for user in users if user.login
        ]


class _LabelPayload(BaseModel):
    name: str = ""
    color: str | None = None


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
    updated_at: datetime | None = None
    created_at: datetime | None = None
    merged_at: datetime | None = None
    author: str = Field("", validation_alias=AliasPath("user", "login"))
    author_id: int | None = Field(None, validation_alias=AliasPath("user", "id"))
    author_avatar_url: str | None = Field(None, validation_alias=AliasPath("user", "avatar_url"))
    head_ref: str = Field("", validation_alias=AliasPath("head", "ref"))
    head_sha: str = Field("", validation_alias=AliasPath("head", "sha"))
    base_ref: str = Field("", validation_alias=AliasPath("base", "ref"))
    base_sha: str = Field("", validation_alias=AliasPath("base", "sha"))
    repo_private: bool | None = Field(None, validation_alias=AliasPath("base", "repo", "private"))
    labels: list[_LabelPayload] = []
    assignees: list[_UserPayload] = []
    requested_reviewers: list[_UserPayload] = []

    @classmethod
    async def fetch(cls, client: httpx2.AsyncClient, owner: str, repo: str, number: int) -> Self:
        response = await github_request(
            client, "GET", f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
        )
        if response.status_code == 404:
            raise HTTPException(404, "not found on GitHub")
        if response.status_code != 200:
            raise HTTPException(502, f"GitHub request failed ({response.status_code})")
        return cls.model_validate(response.json())

    @property
    def pr_state(self) -> PrState:
        return derive_pr_state(state=self.state or None, merged=self.merged, draft=self.draft)

    def to_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        """An unsaved record carrying what GitHub says about the PR."""
        return PullRequest(
            owner=owner,
            repo=repo,
            number=number,
            state=self.pr_state,
            title=self.title,
            body=self.body or "",
            head_ref=self.head_ref,
            base_ref=self.base_ref,
            author=self.author,
            author_github_id=self.author_id,
            additions=self.additions,
            deletions=self.deletions,
            changed_files=self.changed_files,
            head_sha=self.head_sha,
            base_sha=self.base_sha,
            commits=self.commits,
            author_avatar_url=self.author_avatar_url or "",
            labels=[
                LabelRef(name=label.name, color=label.color) for label in self.labels if label.name
            ],
            assignees=_UserPayload.refs(self.assignees),
            requested_reviewers=_UserPayload.refs(self.requested_reviewers),
            github_updated_at=self.updated_at,
            github_created_at=self.created_at,
            merged_at=self.merged_at,
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
        owner, repo, number = self.identity
        return self.pull_request.to_pull_request(owner, repo, number)

    async def mirror(self) -> PullRequest:
        """Save what this event says about the PR and tell open pages it changed."""
        pull_request = self.to_pull_request()
        if pull_request is None:
            raise ValueError("pull_request event names no pull request")
        saved = await pull_request.save(repository_private=self.repo_private)
        await saved.changed_on_github()
        return saved


class PullRequestFilePayload(BaseModel):
    """One entry of ``GET /pulls/{n}/files``."""

    filename: str
    status: FileStatus = "modified"
    previous_filename: str | None = None
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    contents_url: str = ""

    def listed_at_another_head(self, head_sha: str) -> bool:
        """Whether GitHub listed this file at a head other than ``head_sha``.

        Read from ``contents_url``'s ``ref``, which names the head for every
        file but a removed one: that points at the base, where it still exists.
        """
        if self.status == "removed":
            return False
        query = parse_qs(urlsplit(self.contents_url).query)
        return "ref" in query and query["ref"][0] != head_sha

    @classmethod
    def listing_moved(cls, files: Sequence[Self], head_sha: str) -> bool:
        return any(file.listed_at_another_head(head_sha) for file in files)


class PullRequestFile(Base):
    """A file a pull request's current head changes, in GitHub's listing order."""

    __tablename__ = "pull_request_file"

    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str]
    status: Mapped[FileStatus] = mapped_column(Text)
    previous_path: Mapped[str | None] = mapped_column(default=None)
    additions: Mapped[int] = mapped_column(default=0)
    deletions: Mapped[int] = mapped_column(default=0)


type _MirrorKey = tuple[str, str, int]

_PULLS: dict[_MirrorKey, asyncio.Future[PullRequest]] = {}
_REFRESHES: dict[_MirrorKey, asyncio.Task[None]] = {}
