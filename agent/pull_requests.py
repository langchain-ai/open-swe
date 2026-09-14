"""Pull requests as records, owned by a repository and pointing at their threads.

A pull request is the thing Open SWE actually works on across many threads: the
agent thread that opened it, later threads that repair it, and the reviewer
thread that reviews it. Before this record the only way back from a PR to its
threads was a paginated ``threads.search`` over ``pr_url``/``pr_urls`` metadata
followed by a heuristic pick, which cannot answer "which thread is *the* thread"
at all.

Each record names one ``primary`` thread — the thread that opened the PR, or the
first thread associated with it — and any number of secondaries. First writer
wins: once a primary is set, later links are secondary.

Records are keyed by number under a per-repository namespace, so a PR is reached
in one store read from ``(owner, repo, number)`` alone. ``load`` returns an
unsaved record for a PR nobody has written yet, so callers work with a record
either way.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.github.comments import PrState, derive_pr_state
from agent.github.pull_request_status import pull_request_identity
from agent.repositories import Repository
from agent.review.findings import REVIEWER_THREAD_KIND
from agent.store import TypedStore, now_iso
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_pr_state import agent_thread_pr_state_lock

logger = logging.getLogger(__name__)

PULL_REQUESTS_NAMESPACE: list[str] = ["pull_requests"]

ThreadRole = Literal["primary", "secondary"]

_SEARCH_PAGE_SIZE = 50


class ThreadLink(BaseModel):
    model_config = ConfigDict(extra="ignore")

    thread_id: str
    role: ThreadRole = "secondary"
    source: str = ""
    linked_at: str = ""


class ReviewLink(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reviewer_thread_id: str = ""
    github_review_id: int | None = None
    url: str = ""
    head_sha: str = ""
    finding_count: int | None = None
    published_at: str = ""

    def same_as(self, other: ReviewLink) -> bool:
        if other.github_review_id is not None:
            return self.github_review_id == other.github_review_id
        return (
            self.github_review_id is None
            and self.reviewer_thread_id == other.reviewer_thread_id
            and self.head_sha == other.head_sha
        )


class PullRequestActor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str = ""


class PullRequestRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: str = ""


class PullRequestRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str = ""
    private: bool | None = None


class PullRequestPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int | None = None
    html_url: str = ""
    title: str = ""
    state: str = ""
    draft: bool = False
    merged: bool = False
    user: PullRequestActor = Field(default_factory=PullRequestActor)
    head: PullRequestRef = Field(default_factory=PullRequestRef)
    base: PullRequestRef = Field(default_factory=PullRequestRef)


class PullRequestEvent(BaseModel):
    """The slice of a GitHub ``pull_request`` webhook a PR record is built from."""

    model_config = ConfigDict(extra="ignore")

    pull_request: PullRequestPayload
    repository: PullRequestRepository = Field(default_factory=PullRequestRepository)

    @classmethod
    def parse(cls, payload: object) -> Self | None:
        try:
            return cls.model_validate(payload)
        except ValidationError:
            return None

    @property
    def identity(self) -> tuple[str, str, int] | None:
        return pull_request_identity(
            {"repo_full_name": self.repository.full_name, "number": self.pull_request.number}
        )

    @property
    def state(self) -> PrState:
        return derive_pr_state(
            state=self.pull_request.state or None,
            merged=self.pull_request.merged,
            draft=self.pull_request.draft,
        )

    async def record(self) -> PullRequest | None:
        """Upsert the PR record this event describes."""
        if self.identity is None:
            return None
        owner, repo, number = self.identity
        return await PullRequest.record(
            owner=owner,
            repo=repo,
            number=number,
            url=self.pull_request.html_url,
            state=self.state,
            title=self.pull_request.title,
            head_ref=self.pull_request.head.ref,
            base_ref=self.pull_request.base.ref,
            author=self.pull_request.user.login,
            private=self.repository.private,
        )


class PullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo_full_name: str
    owner: str
    repo: str
    number: int
    url: str = ""
    state: PrState = "open"
    title: str = ""
    head_ref: str = ""
    base_ref: str = ""
    author: str = ""
    resolves_thread: bool = False
    threads: list[ThreadLink] = Field(default_factory=list)
    reviews: list[ReviewLink] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def seed(cls, owner: str, repo: str, number: int, *, url: str = "") -> Self:
        return cls(
            repo_full_name=f"{owner}/{repo}",
            owner=owner,
            repo=repo,
            number=number,
            url=url or f"https://github.com/{owner}/{repo}/pull/{number}",
            created_at=now_iso(),
        )

    @classmethod
    def store_for(cls, owner: str, repo: str) -> TypedStore[Self]:
        return TypedStore([*PULL_REQUESTS_NAMESPACE, owner.lower(), repo.lower()], cls)

    @classmethod
    async def get(cls, owner: str, repo: str, number: int) -> Self | None:
        """The stored record, or ``None`` when nothing has written this PR yet."""
        return await cls.store_for(owner, repo).get(str(number))

    @classmethod
    async def load(cls, owner: str, repo: str, number: int) -> Self:
        """The stored record, or an unsaved one, so callers always hold a record."""
        return await cls.get(owner, repo, number) or cls.seed(owner, repo, number)

    @classmethod
    async def for_repository(cls, owner: str, repo: str) -> list[Self]:
        return await cls.store_for(owner, repo).search_all()

    @classmethod
    async def record(
        cls,
        *,
        owner: str,
        repo: str,
        number: int,
        url: str = "",
        state: PrState | None = None,
        title: str | None = None,
        head_ref: str | None = None,
        base_ref: str | None = None,
        author: str | None = None,
        resolves_thread: bool | None = None,
        thread_id: str = "",
        thread_source: str = "",
        private: bool | None = None,
    ) -> Self:
        """Upsert the PR record, creating the repository record alongside it.

        Only fields passed explicitly are written, so a webhook carrying a state
        change cannot blank out a title an earlier writer recorded.
        """
        await Repository.record(f"{owner}/{repo}", private=private)
        async with cls.seed(owner, repo, number, url=url).lock():
            current = await cls.load(owner, repo, number)
            if url:
                current.url = url
            if state is not None:
                current.state = state
            if title is not None:
                current.title = title
            if head_ref is not None:
                current.head_ref = head_ref
            if base_ref is not None:
                current.base_ref = base_ref
            if author is not None:
                current.author = author
            if resolves_thread:
                current.resolves_thread = True
            current.attach_thread(thread_id, source=thread_source)
            return await current.save()

    @property
    def key(self) -> str:
        return str(self.number)

    @property
    def store(self) -> TypedStore[Self]:
        return type(self).store_for(self.owner, self.repo)

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
        """Link a thread in memory, as primary when this PR has none yet."""
        if not thread_id or any(link.thread_id == thread_id for link in self.threads):
            return
        role: ThreadRole = "secondary" if self.primary_thread_id else "primary"
        self.threads.append(
            ThreadLink(thread_id=thread_id, role=role, source=source, linked_at=now_iso())
        )

    async def save(self) -> Self:
        self.updated_at = now_iso()
        return await self.store.put(self.key, self)

    @asynccontextmanager
    async def lock(self) -> AsyncIterator[None]:
        """Serialize read-modify-write on this PR record; the store has no CAS."""
        async with agent_thread_pr_state_lock(
            langgraph_client(), f"pr:{self.owner.lower()}/{self.repo.lower()}#{self.number}"
        ):
            yield

    async def link_thread(self, thread_id: str, *, source: str = "") -> Self:
        """Associate a thread with this PR, as primary when it has none yet."""
        return await type(self).record(
            owner=self.owner,
            repo=self.repo,
            number=self.number,
            thread_id=thread_id,
            thread_source=source,
        )

    async def link_review(
        self,
        *,
        reviewer_thread_id: str = "",
        github_review_id: int | None = None,
        url: str = "",
        head_sha: str = "",
        finding_count: int | None = None,
    ) -> Self:
        """Record a published review against this PR, replacing a same-id entry."""
        await Repository.record(self.repo_full_name)
        review = ReviewLink(
            reviewer_thread_id=reviewer_thread_id,
            github_review_id=github_review_id,
            url=url,
            head_sha=head_sha,
            finding_count=finding_count,
            published_at=now_iso(),
        )
        async with self.lock():
            current = await type(self).load(self.owner, self.repo, self.number)
            current.reviews = [
                existing for existing in current.reviews if not existing.same_as(review)
            ] + [review]
            return await current.save()

    async def linked_threads(self, *, backfill: bool = True) -> list[str]:
        """Linked agent threads, primary first.

        Falls back to the legacy ``pr_url``/``pr_urls`` thread scan for pull
        requests that predate the record, writing what it finds so the scan
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
        record = self
        for thread_id in discovered:
            record = await record.link_thread(thread_id, source="backfill")
        return record.thread_ids

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
