"""Read pull requests from the GitHub API into the mirror, for what webhooks leave out.

``PullRequestSync.refresh`` re-reads the pull request itself, which is where
GitHub's own verdicts live: mergeability (computed after every push, ``null``
meanwhile) and the review decision. ``hydrate`` re-reads everything at once: on
first sight, after a push (commits, files and comment positions all move) and
whenever the sweep finds the mirror behind. Both run as jobs keyed by the pull
request, so a burst of deliveries collapses into one read.

The sweep is the safety net for deliveries GitHub never made or Open SWE
dropped: every ``SWEEP_SCHEDULE`` it lists each repository's pull requests
updated since the last sweep and hydrates the ones the mirror is behind on.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Self

import httpx2
from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, exists, func, select, text, update

from openswe.database import postgres
from openswe.github.http import GitHubAppUnavailable, GitHubClient, RepoClient
from openswe.github.mirror.ci import (
    CheckRun,
    CheckRunPayload,
    CommitStatus,
    StatusPayload,
    WorkflowRun,
    WorkflowRunPayload,
)
from openswe.github.mirror.conversation import (
    CommitPayload,
    FilePayload,
    IssueComment,
    IssueCommentPayload,
    PullRequestCommit,
    PullRequestFile,
    Review,
    ReviewComment,
    ReviewCommentPayload,
    ReviewPayload,
    ReviewThread,
    ThreadNode,
    drop_unlisted,
    replace_listing,
)
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest, PullRequestPayload
from openswe.github.repositories import Repository
from openswe.jobs import queue
from openswe.ui_invalidations import Topic
from openswe.workspaces.routing import repo_is_routable

logger = logging.getLogger(__name__)

SYNC_JOB: Final = "pull_request_sync"
SWEEP_JOB: Final = "pull_request_sweep"
SWEEP_SCHEDULE: Final = "*/15 * * * *"
# Long enough for the deliveries of one push or review to arrive and collapse.
_SETTLE: Final = timedelta(seconds=3)
# GitHub usually decides mergeability within seconds, sometimes minutes.
_MERGEABILITY_RETRIES: Final = (
    timedelta(seconds=3),
    timedelta(seconds=10),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
)
_FILE_PAGES: Final = 30
# A sweep looks back this far past its watermark, for clocks and slow listings.
_SWEEP_OVERLAP: Final = timedelta(minutes=5)
# Checks still running this long after a hydrate were probably missed.
_STALE_PENDING: Final = timedelta(minutes=30)
# CI on commits no open pull request has at its head is kept this long.
CI_RETENTION: Final = timedelta(days=14)
_OPEN_STATES: Final = ("open", "draft")
_INTERRUPTED: Final = frozenset({"cancelled", "stale"})


def _parse[M: BaseModel](model: type[M], items: list[dict[str, Any]], *, what: str) -> list[M]:
    """The ``items`` that read as ``model``; GitHub adding a shape must not stop a sync."""
    parsed: list[M] = []
    for item in items:
        try:
            parsed.append(model.model_validate(item))
        except ValidationError:
            logger.warning(
                "Skipped an unreadable GitHub record", extra={"github_record": what}, exc_info=True
            )
    return parsed


@dataclass(frozen=True, slots=True)
class PullRequestSync:
    """One pull request's reads from GitHub into the mirror, as the App."""

    owner: str
    repo: str
    number: int

    @property
    def key(self) -> PullRequestKey:
        return PullRequestKey.of(self.owner, self.repo, self.number)

    @property
    def _log_extra(self) -> dict[str, object]:
        return {"pr_repo_full_name": f"{self.owner}/{self.repo}", "pr_number": self.number}

    async def schedule(self, *, full: bool, after: timedelta = _SETTLE, attempt: int = 0) -> None:
        """Queue a refresh, or with ``full`` a hydrate, unless an earlier one is already due."""
        key = f"{SYNC_JOB}:{'hydrate' if full else 'refresh'}:{self.key}"
        due = datetime.now(UTC) + after
        queued = await queue.get(key)
        if queued is not None and queued.claim_token is None and queued.due_at <= due:
            return
        await queue.schedule(
            SYNC_JOB,
            {
                "repository": f"{self.owner}/{self.repo}",
                "number": self.number,
                "full": full,
                "attempt": attempt,
            },
            due_at=due,
            key=key,
        )

    async def run(self, *, full: bool, attempt: int = 0) -> None:
        try:
            stored = await (self.hydrate() if full else self.refresh())
        except GitHubAppUnavailable:
            logger.info("No GitHub App installation to sync a pull request", extra=self._log_extra)
            return
        if (
            stored.state in _OPEN_STATES
            and stored.mergeable is None
            and attempt < len(_MERGEABILITY_RETRIES)
        ):
            await self.schedule(
                full=False, after=_MERGEABILITY_RETRIES[attempt], attempt=attempt + 1
            )

    async def refresh(self) -> PullRequest:
        """Re-read the pull request and GitHub's verdicts on it."""
        async with GitHubClient.as_app(self.owner, self.repo) as github:
            client = github.repo(self.owner, self.repo).pull_request(self.number)
            pull, decision = await asyncio.gather(client.pull(), client.review_decision())
        record = PullRequestPayload.model_validate(pull).to_pull_request(
            self.owner, self.repo, self.number
        )
        record.review_decision = decision
        stored = await record.save()
        await Topic.PULL_REQUESTS.invalidate(key=self.key)
        return stored

    async def hydrate(self) -> PullRequest:
        """Re-read everything GitHub holds for the pull request, and drop what it no longer does."""
        async with postgres.transaction() as conn:
            read_at: datetime = (await conn.execute(select(func.clock_timestamp()))).scalar_one()
        async with GitHubClient.as_app(self.owner, self.repo) as github:
            repository = github.repo(self.owner, self.repo)
            client = repository.pull_request(self.number)
            pull = await client.pull()
            head = PullRequestPayload.model_validate(pull).head_sha
            decision, snapshot = await asyncio.gather(
                client.review_decision(), _Snapshot.read(repository, client, head)
            )
        record = PullRequestPayload.model_validate(pull).to_pull_request(
            self.owner, self.repo, self.number
        )
        record.review_decision = decision
        stored = await record.save()
        await snapshot.store(stored, head, read_at=read_at)
        stored.synced_at, stored.synced_head_sha = read_at, head
        logger.info("Hydrated pull request from GitHub", extra=self._log_extra)
        return stored

    async def mirrored(self) -> PullRequest | None:
        """The stored pull request, read whole from GitHub first if it never has been.

        ``None`` when nothing is stored and GitHub could not be read either.
        """
        stored = await PullRequest.get(self.owner, self.repo, self.number)
        if stored is not None and stored.synced_at is not None:
            return stored
        try:
            return await self.hydrate()
        except GitHubAppUnavailable:
            logger.info("No GitHub App installation to sync a pull request", extra=self._log_extra)
        except httpx2.HTTPError, ValueError:
            logger.warning(
                "Could not read a pull request from GitHub", extra=self._log_extra, exc_info=True
            )
        return stored


@dataclass(frozen=True, slots=True)
class _Snapshot:
    reviews: list[ReviewPayload]
    review_comments: list[ReviewCommentPayload]
    issue_comments: list[IssueCommentPayload]
    threads: list[ThreadNode]
    commits: list[CommitPayload]
    files: list[FilePayload]
    runs: list[CheckRunPayload]
    statuses: list[StatusPayload]
    workflows: list[WorkflowRunPayload]

    @classmethod
    async def read(cls, repository: RepoClient, client: PullRequestClient, head: str) -> Self:
        """Everything GitHub lists for the pull request and the CI on its ``head``."""
        async with asyncio.TaskGroup() as group:
            reviews = group.create_task(client.reviews())
            review_comments = group.create_task(client.review_comments())
            issue_comments = group.create_task(client.issue_comments())
            threads = group.create_task(client.review_threads())
            commits = group.create_task(client.commits())
            files = group.create_task(client.files(max_pages=_FILE_PAGES))
            runs = group.create_task(repository.latest_check_runs(head)) if head else None
            statuses = group.create_task(repository.commit_statuses(head)) if head else None
        run_items = runs.result() if runs is not None else []
        interrupted = any(run.get("conclusion") in _INTERRUPTED for run in run_items)
        workflows = await repository.workflow_runs(head) if interrupted else []
        parsed_reviews = _parse(ReviewPayload, reviews.result(), what="review")
        # A pending review's comments are its author's alone until it is submitted.
        pending = {review.id for review in parsed_reviews if review.state == "pending"}
        return cls(
            reviews=parsed_reviews,
            review_comments=[
                comment
                for comment in _parse(
                    ReviewCommentPayload, review_comments.result(), what="review_comment"
                )
                if comment.pull_request_review_id not in pending
            ],
            issue_comments=_parse(
                IssueCommentPayload, issue_comments.result(), what="issue_comment"
            ),
            threads=_parse(ThreadNode, threads.result(), what="review_thread"),
            commits=_parse(CommitPayload, commits.result(), what="commit"),
            files=_parse(FilePayload, files.result(), what="file"),
            runs=_parse(CheckRunPayload, run_items, what="check_run"),
            statuses=_parse(
                StatusPayload,
                statuses.result() if statuses is not None else [],
                what="commit_status",
            ),
            workflows=_parse(WorkflowRunPayload, workflows, what="workflow_run"),
        )

    async def store(self, pull_request: PullRequest, head: str, *, read_at: datetime) -> None:
        pr_id, repository_id = pull_request.id, pull_request.repository_id
        reviews = [row for row in (r.to_row(pr_id) for r in self.reviews) if row is not None]
        threads = [row for row in (t.to_row(pr_id) for t in self.threads) if row is not None]
        async with postgres.session() as session:
            await Review.store(session, reviews)
            await drop_unlisted(
                session, Review, pr_id, [r.github_id for r in reviews], read_at=read_at
            )
            await ReviewComment.store(session, [c.to_row(pr_id) for c in self.review_comments])
            await drop_unlisted(
                session,
                ReviewComment,
                pr_id,
                [c.id for c in self.review_comments],
                read_at=read_at,
            )
            await ReviewThread.put(session, threads)
            await session.execute(
                delete(ReviewThread).where(
                    ReviewThread.pull_request_id == pr_id,
                    ReviewThread.root_comment_github_id.not_in(
                        [t.root_comment_github_id for t in threads]
                    ),
                    ReviewThread.updated_at < read_at,
                )
            )
            await IssueComment.store(session, [c.to_row(pr_id) for c in self.issue_comments])
            await drop_unlisted(
                session,
                IssueComment,
                pr_id,
                [c.id for c in self.issue_comments],
                read_at=read_at,
            )
            await replace_listing(
                session,
                PullRequestCommit,
                pr_id,
                [c.to_row(pr_id, position) for position, c in enumerate(self.commits)],
            )
            await replace_listing(
                session, PullRequestFile, pr_id, [f.to_row(pr_id) for f in self.files]
            )
            await CheckRun.store(session, [r.to_row(repository_id) for r in self.runs])
            await CommitStatus.store(
                session, [s.to_row(repository_id, head) for s in self.statuses]
            )
            await WorkflowRun.put(session, [w.to_row(repository_id) for w in self.workflows])
            await session.execute(
                update(PullRequest)
                .where(PullRequest.id == pr_id)
                .values(synced_at=read_at, synced_head_sha=head)
            )
            await Topic.PULL_REQUESTS.invalidate(
                session,
                key=PullRequestKey.of(pull_request.owner, pull_request.repo, pull_request.number),
            )


@dataclass(frozen=True, slots=True)
class RepositorySweep:
    """Compares one repository's pull requests on GitHub with the mirror."""

    repository: Repository

    @classmethod
    async def schedule(cls, full_name: str) -> None:
        """Sweep ``full_name`` soon instead of at the next standing sweep."""
        await queue.schedule(
            SWEEP_JOB,
            {"repository": full_name},
            due_at=datetime.now(UTC) + _SETTLE,
            key=f"{SWEEP_JOB}:{full_name.lower()}",
        )

    async def run(self) -> int:
        """How many pull requests it queued for hydration."""
        repository = self.repository
        if not await repo_is_routable(repository.owner, repository.name):
            return 0
        since = repository.pull_requests_swept_through
        async with GitHubClient.as_app(repository.owner, repository.name) as github:
            listed = await github.repo(repository.owner, repository.name).pulls_updated_since(
                None if since is None else since - _SWEEP_OVERLAP
            )
        pulls = [PullRequestPayload.model_validate(item) for item in listed]
        stored = await self._stored_versions([p.number for p in pulls if p.number is not None])
        behind = [
            pull
            for pull in pulls
            if pull.number is not None
            and (
                (version := stored.get(pull.number)) is None
                or version[0] is None
                or (
                    pull.updated_at is not None
                    and (version[1] is None or pull.updated_at > version[1])
                )
            )
        ]
        stale = await self._stale_open()
        numbers = {pull.number for pull in behind if pull.number is not None} | stale
        for number in numbers:
            await PullRequestSync(repository.owner, repository.name, number).schedule(full=True)
        newest = max((p.updated_at for p in pulls if p.updated_at is not None), default=None)
        watermark = max(filter(None, (since, newest)), default=datetime.now(UTC))
        async with postgres.session() as session:
            await session.execute(
                update(Repository)
                .where(Repository.id == repository.id)
                .values(pull_requests_swept_through=watermark)
            )
        return len(numbers)

    async def _stored_versions(
        self, numbers: list[int]
    ) -> dict[int, tuple[datetime | None, datetime | None]]:
        """``(synced_at, github_updated_at)`` of the stored pull requests among ``numbers``."""
        if not numbers:
            return {}
        async with postgres.session() as session:
            rows = await session.execute(
                select(
                    PullRequest.number, PullRequest.synced_at, PullRequest.github_updated_at
                ).where(
                    PullRequest.repository_id == self.repository.id,
                    PullRequest.number.in_(numbers),
                )
            )
            return {number: (synced, updated) for number, synced, updated in rows}

    async def _stale_open(self) -> set[int]:
        """Open pull requests whose head checks have been pending since well before their last hydrate."""
        cutoff = datetime.now(UTC) - _STALE_PENDING
        async with postgres.session() as session:
            rows = await session.scalars(
                select(PullRequest.number).where(
                    PullRequest.repository_id == self.repository.id,
                    PullRequest.state.in_(_OPEN_STATES),
                    PullRequest.synced_at < cutoff,
                    exists().where(
                        CheckRun.repository_id == PullRequest.repository_id,
                        CheckRun.head_sha == PullRequest.head_sha,
                        CheckRun.status != "completed",
                        CheckRun.updated_at < cutoff,
                    ),
                )
            )
            return set(rows)


async def sweep(full_name: str | None = None) -> dict[str, int]:
    """Sweep ``full_name``, or every repository, and prune CI nobody shows."""
    if full_name is None:
        repositories = await Repository.all()
    else:
        repository = await Repository.get(full_name)
        repositories = [repository] if repository is not None else []
    queued: dict[str, int] = {}
    for repository in repositories:
        try:
            queued[repository.full_name] = await RepositorySweep(repository).run()
        except GitHubAppUnavailable:
            logger.info(
                "No GitHub App installation to sweep pull requests",
                extra={"repository": repository.full_name},
            )
        except httpx2.HTTPError, ValueError, ValidationError:
            logger.warning(
                "Pull request sweep failed for a repository",
                extra={"repository": repository.full_name},
                exc_info=True,
            )
    if full_name is None:
        await prune_ci()
    return queued


async def prune_ci() -> None:
    """Drop CI older than ``CI_RETENTION`` on commits no open pull request has at its head."""
    async with postgres.transaction() as conn:
        for table, sha in (
            ("github_check_run", "head_sha"),
            ("github_commit_status", "sha"),
            ("github_workflow_run", "head_sha"),
        ):
            await conn.execute(
                text(
                    f"DELETE FROM {table} AS ci "
                    "WHERE ci.updated_at < clock_timestamp() - CAST(:age AS interval) "
                    "AND NOT EXISTS (SELECT 1 FROM pull_request pr "
                    "WHERE pr.repository_id = ci.repository_id "
                    f"AND pr.head_sha = ci.{sha} AND pr.state IN ('open', 'draft'))"
                ),
                {"age": CI_RETENTION},
            )


async def start_sweeps() -> None:
    await queue.schedule(SWEEP_JOB, cron=SWEEP_SCHEDULE, key=SWEEP_JOB)
