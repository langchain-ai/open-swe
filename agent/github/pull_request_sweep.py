"""Backup full synchronisation for pull requests no webhook reached.

Webhook deliveries go missing — outages, re-installed apps, hooks pointed at a
retired deployment — and a pull request row then drifts from GitHub silently.
The sweep is the one writer that runs without an event: it takes the open pull
requests whose ``last_synced_at`` is oldest (or absent), mints one GitHub App
installation token per repository, and hands each pull request to
``agent.github.pull_request_sync``.

Nothing here raises. A repository without an installation token counts as
skipped, a pull request GitHub would not hand over counts as failed, and the
sweep moves on to the next one.
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from agent.config import ENV
from agent.database import configured, postgres
from agent.github.app import get_github_app_installation_token
from agent.github.pull_request_sync import (
    sync_pull_request,
    sync_repository_open_pull_requests,
)
from agent.github.pull_requests import PullRequest
from agent.github.repositories import Repository

logger = logging.getLogger(__name__)

OPEN_STATES = ("open", "draft")

_SWEEP_CONCURRENCY = 4
_DEFAULT_INTERVAL_SECONDS = 15 * 60
_DEFAULT_MAX_AGE_SECONDS = 60 * 60
_DEFAULT_BATCH = 50

_STOP = asyncio.Event()
_TASK: asyncio.Task[None] | None = None
_RUNNING = asyncio.Lock()


class RepositorySweep(BaseModel):
    """What one repository's slice of a sweep did."""

    repo_full_name: str
    synced: int = 0
    failed: int = 0
    skipped: int = 0


class SweepReport(BaseModel):
    synced: int = 0
    failed: int = 0
    skipped: int = 0
    repositories: list[RepositorySweep] = Field(default_factory=list)

    def add(self, repository: RepositorySweep) -> None:
        self.synced += repository.synced
        self.failed += repository.failed
        self.skipped += repository.skipped
        self.repositories.append(repository)


async def sweep_stale_pull_requests(*, max_age: timedelta, limit: int) -> SweepReport:
    """Re-read the ``limit`` least recently synced open pull requests."""
    report = SweepReport()
    if limit <= 0:
        return report
    for repo_full_name, numbers in (await _stale_pull_requests(max_age, limit)).items():
        owner, repo = repo_full_name.split("/", 1)
        report.add(await _sweep_numbers(owner, repo, numbers))
    return report


async def sweep_repository(owner: str, repo: str) -> SweepReport:
    """Re-read every pull request GitHub still calls open, then settle the rest.

    A pull request stored as open that GitHub no longer lists was closed or
    merged while the webhook was missing; only an individual read records that.
    """
    repo_full_name = f"{owner}/{repo}"
    report = SweepReport()
    stored = await _stored_open_numbers(owner, repo)
    token = await _installation_token(repo_full_name)
    if token is None:
        report.add(RepositorySweep(repo_full_name=repo_full_name, skipped=len(stored)))
        return report
    try:
        listed = await sync_repository_open_pull_requests(owner, repo, token=token)
    except Exception:
        logger.warning(
            "Pull request sweep could not sync a repository's open pull requests",
            extra={"pr_repo_full_name": repo_full_name},
            exc_info=True,
        )
        listed = []
    listed_numbers = {row.number for row in listed}
    synced, failed = await _sync_numbers(
        owner,
        repo,
        [number for number in stored if number not in listed_numbers],
        token=token,
    )
    report.add(
        RepositorySweep(repo_full_name=repo_full_name, synced=len(listed) + synced, failed=failed)
    )
    return report


async def run_sweep_once() -> SweepReport:
    """One configured stale sweep, skipped when another is already in flight."""
    if _RUNNING.locked():
        logger.info("Pull request sweep is already running; skipping this pass")
        return SweepReport()
    async with _RUNNING:
        return await sweep_stale_pull_requests(
            max_age=timedelta(
                seconds=ENV.PULL_REQUEST_SYNC_MAX_AGE_SECONDS.get_int(_DEFAULT_MAX_AGE_SECONDS)
            ),
            limit=ENV.PULL_REQUEST_SYNC_BATCH.get_int(_DEFAULT_BATCH),
        )


async def start_sweeper() -> None:
    global _TASK
    interval = ENV.PULL_REQUEST_SYNC_INTERVAL_SECONDS.get_int(_DEFAULT_INTERVAL_SECONDS)
    if interval <= 0 or not configured() or (_TASK is not None and not _TASK.done()):
        return
    _STOP.clear()
    _TASK = asyncio.create_task(_sweep_loop(interval), name="pull-request-sweeper")


async def stop_sweeper() -> None:
    global _TASK
    _STOP.set()
    if _TASK is not None:
        await _TASK
    _TASK = None


async def _sweep_loop(interval: int) -> None:
    while not _STOP.is_set():
        try:
            await asyncio.wait_for(_STOP.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        report = await run_sweep_once()
        if report.repositories:
            logger.info(
                "Pull request sweep finished",
                extra={
                    "pr_sweep_synced": report.synced,
                    "pr_sweep_failed": report.failed,
                    "pr_sweep_skipped": report.skipped,
                    "pr_sweep_repositories": len(report.repositories),
                },
            )


async def _stale_pull_requests(max_age: timedelta, limit: int) -> dict[str, list[int]]:
    """Stale open pull requests, oldest sync first, grouped by repository."""
    cutoff = datetime.now(UTC) - max_age
    grouped: dict[str, list[int]] = {}
    try:
        async with postgres.session() as session:
            rows = await session.execute(
                select(PullRequest.owner, PullRequest.repo, PullRequest.number)
                .where(
                    PullRequest.state.in_(OPEN_STATES),
                    or_(
                        PullRequest.last_synced_at.is_(None),
                        PullRequest.last_synced_at < cutoff,
                    ),
                )
                .order_by(PullRequest.last_synced_at.asc().nulls_first())
                .limit(limit)
            )
            for owner, repo, number in rows:
                grouped.setdefault(f"{owner}/{repo}", []).append(number)
    except Exception:
        logger.warning("Pull request sweep could not select stale pull requests", exc_info=True)
    return grouped


async def _stored_open_numbers(owner: str, repo: str) -> list[int]:
    try:
        async with postgres.session() as session:
            rows = await session.scalars(
                select(PullRequest.number)
                .join(PullRequest.repository)
                .where(
                    Repository.key == f"{owner}/{repo}".lower(),
                    PullRequest.state.in_(OPEN_STATES),
                )
                .order_by(PullRequest.number)
            )
            return list(rows)
    except Exception:
        logger.warning(
            "Pull request sweep could not read a repository's stored pull requests",
            extra={"pr_repo_full_name": f"{owner}/{repo}"},
            exc_info=True,
        )
        return []


async def _sweep_numbers(owner: str, repo: str, numbers: Sequence[int]) -> RepositorySweep:
    repo_full_name = f"{owner}/{repo}"
    token = await _installation_token(repo_full_name)
    if token is None:
        return RepositorySweep(repo_full_name=repo_full_name, skipped=len(numbers))
    synced, failed = await _sync_numbers(owner, repo, numbers, token=token)
    return RepositorySweep(repo_full_name=repo_full_name, synced=synced, failed=failed)


async def _sync_numbers(
    owner: str, repo: str, numbers: Sequence[int], *, token: str
) -> tuple[int, int]:
    if not numbers:
        return 0, 0
    semaphore = asyncio.Semaphore(_SWEEP_CONCURRENCY)

    async def one(number: int) -> bool:
        async with semaphore:
            return await _sync_one(owner, repo, number, token=token)

    results = await asyncio.gather(*(one(number) for number in numbers))
    synced = sum(1 for ok in results if ok)
    return synced, len(results) - synced


async def _sync_one(owner: str, repo: str, number: int, *, token: str) -> bool:
    try:
        return await sync_pull_request(owner, repo, number, token=token) is not None
    except Exception:
        logger.warning(
            "Pull request sweep failed to sync a pull request",
            extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
            exc_info=True,
        )
        return False


async def _installation_token(repo_full_name: str) -> str | None:
    try:
        token = await get_github_app_installation_token(repositories=[repo_full_name])
    except Exception:
        logger.warning(
            "Pull request sweep could not mint an installation token",
            extra={"pr_repo_full_name": repo_full_name},
            exc_info=True,
        )
        return None
    if token is None:
        logger.warning(
            "Pull request sweep has no installation token for this repository",
            extra={"pr_repo_full_name": repo_full_name},
        )
    return token
