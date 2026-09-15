"""Pull requests as rows, owned by a repository and pointing at their threads.

A pull request is the thing Open SWE actually works on across many threads: the
agent thread that opened it, later threads that repair it, and the reviewer
thread that reviews it. Before these tables the only way back from a PR to its
threads was a paginated ``threads.search`` over ``pr_url``/``pr_urls`` metadata
followed by a heuristic pick, which cannot answer "which thread is *the* thread"
at all.

Each pull request names one ``primary`` thread — the thread that opened the PR,
or the first thread associated with it — and any number of secondaries. First
writer wins: ``save`` upserts the pull request row first, which locks it for the
rest of the transaction, so the role it assigns to each new link is decided
against links that have already committed, and a partial unique index makes a
second primary impossible.

Rows live in PostgreSQL (``POSTGRES_URI``), which this module requires. Entity
rows carry a synthetic UUIDv7 ``id``; ``(repository, number)`` stays the natural
key callers address a PR by. ``save`` merges: fields set on the instance win,
threads and reviews accrue.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid7

from pydantic import AliasPath, BaseModel, Field, ValidationError
from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agent.database import postgres
from agent.database.rows import (
    PullRequestReviewRow,
    PullRequestRow,
    PullRequestThreadRow,
    RepositoryRow,
)
from agent.github.comments import PrState, derive_pr_state
from agent.github.pull_request_status import pull_request_identity
from agent.repositories import Repository
from agent.review.findings import REVIEWER_THREAD_KIND
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

ThreadRole = Literal["primary", "secondary"]

_SEARCH_PAGE_SIZE = 50
_MUTABLE_COLUMNS = ("state", "title", "head_ref", "base_ref", "author", "resolves_thread")


def _with_links(statement: Select[tuple[PullRequestRow]]) -> Select[tuple[PullRequestRow]]:
    return statement.options(
        selectinload(PullRequestRow.threads), selectinload(PullRequestRow.reviews)
    )


class ThreadLink(BaseModel):
    thread_id: str
    role: ThreadRole = "secondary"
    source: str = ""
    linked_at: datetime | None = None


class ReviewLink(BaseModel):
    id: UUID | None = None
    reviewer_thread_id: str = ""
    github_review_id: int | None = None
    url: str = ""
    head_sha: str = ""
    finding_count: int | None = None
    published_at: datetime | None = None

    def same_as(self, row: PullRequestReviewRow) -> bool:
        if self.github_review_id is not None:
            return row.github_review_id == self.github_review_id
        return (
            row.github_review_id is None
            and row.reviewer_thread_id == self.reviewer_thread_id
            and row.head_sha == self.head_sha
        )


class PullRequest(BaseModel):
    owner: str
    repo: str
    number: int
    id: UUID | None = None
    state: PrState = "open"
    title: str = ""
    head_ref: str = ""
    base_ref: str = ""
    author: str = ""
    resolves_thread: bool = False
    threads: list[ThreadLink] = Field(default_factory=list)
    reviews: list[ReviewLink] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

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
                _with_links(
                    select(PullRequestRow)
                    .join(PullRequestRow.repository)
                    .where(RepositoryRow.key == f"{owner}/{repo}".lower())
                    .order_by(PullRequestRow.number)
                )
            )
            return [cls.model_validate(row, from_attributes=True) for row in rows]

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

    def attach_thread(self, thread_id: str, *, source: str = "") -> None:
        """Queue a thread link for the next ``save``; the row decides its role."""
        if thread_id and all(link.thread_id != thread_id for link in self.threads):
            self.threads.append(ThreadLink(thread_id=thread_id, source=source))

    def attach_review(self, review: ReviewLink) -> None:
        """Queue a review for the next ``save``; a same-identity row is updated in place."""
        self.reviews.append(review)

    async def save(self, *, repository_private: bool | None = None) -> Self:
        """Merge into the stored row and register the repository it belongs to.

        Fields set on this instance overwrite; threads are linked under the
        pull request's row lock, so the primary is whichever link lands first.
        """
        async with postgres.session() as session:
            repository = await Repository(
                full_name=self.repo_full_name, private=repository_private
            ).save(session)
            if repository.id is None:
                raise RuntimeError(f"repository {self.repo_full_name} saved without an id")
            row = await self._upsert_row(session, repository.id)
            for link in self.threads:
                if all(existing.thread_id != link.thread_id for existing in row.threads):
                    role = (
                        "secondary" if any(t.role == "primary" for t in row.threads) else "primary"
                    )
                    row.threads.append(
                        PullRequestThreadRow(
                            thread_id=link.thread_id, role=role, source=link.source
                        )
                    )
            for review in self.reviews:
                existing = next((r for r in row.reviews if review.same_as(r)), None)
                if existing is None:
                    row.reviews.append(
                        PullRequestReviewRow(
                            id=review.id or uuid7(),
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

    async def link_thread(self, thread_id: str, *, source: str = "") -> Self:
        """Associate a thread with this PR, as primary when it has none yet."""
        self.attach_thread(thread_id, source=source)
        return await self.save()

    async def link_review(
        self,
        *,
        reviewer_thread_id: str = "",
        github_review_id: int | None = None,
        head_sha: str = "",
        finding_count: int | None = None,
    ) -> Self:
        """Record a published review against this PR."""
        self.attach_review(
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
        return await self.save()

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
        for thread_id in discovered:
            self.attach_thread(thread_id, source="backfill")
        return (await self.save()).thread_ids if discovered else []

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
        row = await session.scalar(
            _with_links(
                select(PullRequestRow)
                .join(PullRequestRow.repository)
                .where(
                    RepositoryRow.key == self.repo_full_name.lower(),
                    PullRequestRow.number == self.number,
                )
                .execution_options(populate_existing=True)
            )
        )
        return None if row is None else type(self).model_validate(row, from_attributes=True)

    async def _upsert_row(self, session: AsyncSession, repository_id: UUID) -> PullRequestRow:
        """Insert or update the row and return it locked for the transaction."""
        columns = {c: getattr(self, c) for c in _MUTABLE_COLUMNS if c in self.model_fields_set}
        upsert = insert(PullRequestRow).values(
            id=self.id or uuid7(),
            repository_id=repository_id,
            number=self.number,
            owner=self.owner,
            repo=self.repo,
            **columns,
        )
        pull_request_id = await session.scalar(
            upsert.on_conflict_do_update(
                index_elements=[PullRequestRow.repository_id, PullRequestRow.number],
                set_={
                    **{c: getattr(upsert.excluded, c) for c in columns},
                    "updated_at": func.clock_timestamp(),
                },
            ).returning(PullRequestRow.id)
        )
        row = await session.scalar(
            _with_links(
                select(PullRequestRow)
                .where(PullRequestRow.id == pull_request_id)
                .execution_options(populate_existing=True)
            )
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
