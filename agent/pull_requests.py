"""Pull requests as rows, owned by a repository and pointing at their threads.

A pull request is the thing Open SWE actually works on across many threads: the
agent thread that opened it, later threads that repair it, and the reviewer
thread that reviews it. Before these tables the only way back from a PR to its
threads was a paginated ``threads.search`` over ``pr_url``/``pr_urls`` metadata
followed by a heuristic pick, which cannot answer "which thread is *the* thread"
at all.

Each pull request names one ``primary`` thread — the thread that opened the PR,
or the first thread associated with it — and any number of secondaries. First
writer wins: the role is assigned inside the transaction that inserts the link,
under the row lock ``save`` takes on the pull request, and a partial unique
index makes a second primary impossible.

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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres
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
_PULL_REQUEST_COLUMNS = (
    "pr.id, pr.owner, pr.repo, pr.number, pr.state, pr.title, pr.head_ref, pr.base_ref, "
    "pr.author, pr.resolves_thread, pr.created_at, pr.updated_at"
)
_PULL_REQUEST_FROM = (
    "FROM pull_request pr JOIN repository r ON r.id = pr.repository_id "
    "WHERE r.key = :key AND pr.number = :number"
)
_THREAD_COLUMNS = "thread_id, role, source, linked_at"
_REVIEW_COLUMNS = (
    "id, reviewer_thread_id, github_review_id, url, head_sha, finding_count, published_at"
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
        async with postgres.connection() as conn:
            return await cls(owner=owner, repo=repo, number=number).fetch(conn)

    @classmethod
    async def load(cls, owner: str, repo: str, number: int) -> Self:
        """The stored row, or an unsaved one, so callers always hold a record."""
        return await cls.get(owner, repo, number) or cls(owner=owner, repo=repo, number=number)

    @classmethod
    async def for_repository(cls, owner: str, repo: str) -> list[Self]:
        async with postgres.connection() as conn:
            numbers = (
                await conn.execute(
                    text(
                        "SELECT pr.number FROM pull_request pr "
                        "JOIN repository r ON r.id = pr.repository_id "
                        "WHERE r.key = :key ORDER BY pr.number"
                    ),
                    {"key": f"{owner}/{repo}".lower()},
                )
            ).scalars()
            records: list[Self] = []
            for number in numbers:
                record = await cls(owner=owner, repo=repo, number=number).fetch(conn)
                if record is not None:
                    records.append(record)
            return records

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
        """Queue a review for the next ``save``; a same-identity row is replaced."""
        self.reviews.append(review)

    async def save(self, *, repository_private: bool | None = None) -> Self:
        """Merge into the stored row and register the repository it belongs to.

        Fields set on this instance overwrite; threads are inserted under the
        pull request's row lock, so the primary is whichever link lands first.
        """
        async with postgres.transaction() as conn:
            repository = await Repository(
                full_name=self.repo_full_name, private=repository_private
            ).save(conn)
            if repository.id is None:
                raise RuntimeError(f"repository {self.repo_full_name} saved without an id")
            pull_request_id = await self._upsert_row(conn, repository.id)
            for link in self.threads:
                await self._insert_thread(conn, pull_request_id, link)
            for review in self.reviews:
                await self._replace_review(conn, pull_request_id, review)
            stored = await self.fetch(conn)
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

    async def fetch(self, conn: AsyncConnection) -> Self | None:
        """This PR's stored row with its threads and reviews, read through ``conn``."""
        natural_key = {"key": self.repo_full_name.lower(), "number": self.number}
        row = (
            (
                await conn.execute(
                    text(f"SELECT {_PULL_REQUEST_COLUMNS} {_PULL_REQUEST_FROM}"), natural_key
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        by_pull_request = {"pull_request_id": row["id"]}
        threads = (
            await conn.execute(
                text(
                    f"SELECT {_THREAD_COLUMNS} FROM pull_request_thread "
                    "WHERE pull_request_id = :pull_request_id "
                    "ORDER BY role = 'primary' DESC, linked_at, thread_id"
                ),
                by_pull_request,
            )
        ).mappings()
        reviews = (
            await conn.execute(
                text(
                    f"SELECT {_REVIEW_COLUMNS} FROM pull_request_review "
                    "WHERE pull_request_id = :pull_request_id ORDER BY published_at, id"
                ),
                by_pull_request,
            )
        ).mappings()
        return type(self).model_validate(
            {
                **dict(row),
                "threads": [dict(thread) for thread in threads],
                "reviews": [dict(review) for review in reviews],
            }
        )

    async def _upsert_row(self, conn: AsyncConnection, repository_id: UUID) -> UUID:
        """Insert or update the row, taking its row lock for the transaction; returns its id."""
        columns = [column for column in _MUTABLE_COLUMNS if column in self.model_fields_set]
        insert_columns = ", ".join(["id", "repository_id", "number", "owner", "repo", *columns])
        insert_values = ", ".join(
            [":id", ":repository_id", ":number", ":owner", ":repo", *(f":{c}" for c in columns)]
        )
        updates = ", ".join(
            [*(f"{c} = EXCLUDED.{c}" for c in columns), "updated_at = clock_timestamp()"]
        )
        return await conn.scalar(
            text(
                f"INSERT INTO pull_request ({insert_columns}) VALUES ({insert_values}) "
                f"ON CONFLICT (repository_id, number) DO UPDATE SET {updates} RETURNING id"
            ),
            {
                "id": self.id or uuid7(),
                "repository_id": repository_id,
                "number": self.number,
                "owner": self.owner,
                "repo": self.repo,
                **{column: getattr(self, column) for column in columns},
            },
        )

    async def _insert_thread(
        self, conn: AsyncConnection, pull_request_id: UUID, link: ThreadLink
    ) -> None:
        await conn.execute(
            text(
                "INSERT INTO pull_request_thread (pull_request_id, thread_id, role, source) "
                "SELECT :pull_request_id, :thread_id, CASE WHEN EXISTS ("
                "SELECT 1 FROM pull_request_thread "
                "WHERE pull_request_id = :pull_request_id AND role = 'primary'"
                ") THEN 'secondary' ELSE 'primary' END, :source "
                "ON CONFLICT (pull_request_id, thread_id) DO NOTHING"
            ),
            {
                "pull_request_id": pull_request_id,
                "thread_id": link.thread_id,
                "source": link.source,
            },
        )

    async def _replace_review(
        self, conn: AsyncConnection, pull_request_id: UUID, review: ReviewLink
    ) -> None:
        params = {
            "id": review.id or uuid7(),
            "pull_request_id": pull_request_id,
            "reviewer_thread_id": review.reviewer_thread_id,
            "github_review_id": review.github_review_id,
            "url": review.url,
            "head_sha": review.head_sha,
            "finding_count": review.finding_count,
        }
        same_identity = (
            "github_review_id = :github_review_id"
            if review.github_review_id is not None
            else "github_review_id IS NULL AND reviewer_thread_id = :reviewer_thread_id "
            "AND head_sha = :head_sha"
        )
        await conn.execute(
            text(
                "DELETE FROM pull_request_review "
                f"WHERE pull_request_id = :pull_request_id AND {same_identity}"
            ),
            params,
        )
        await conn.execute(
            text(
                "INSERT INTO pull_request_review (id, pull_request_id, reviewer_thread_id, "
                "github_review_id, url, head_sha, finding_count) VALUES (:id, :pull_request_id, "
                ":reviewer_thread_id, :github_review_id, :url, :head_sha, :finding_count)"
            ),
            params,
        )


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
