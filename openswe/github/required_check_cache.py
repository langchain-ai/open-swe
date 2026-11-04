"""PostgreSQL required-check snapshots refreshed outside PR status requests."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import text

from openswe.database.postgres import session, transaction
from openswe.github.ci import RequiredCheck
from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.github.repositories import Repository
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.ui_invalidations.topics import Topic
from openswe.users.models import User

logger = logging.getLogger(__name__)

REFRESH_JOB = "required_check_cache"
CACHE_TTL = timedelta(minutes=10)


class CachedCheck(BaseModel):
    name: str
    app_id: int | None = None


CHECKS = TypeAdapter(list[CachedCheck])


@dataclass(frozen=True)
class RequiredCheckCache:
    repo: str
    branch: str

    async def read(self, login: str | None = None) -> set[RequiredCheck] | None:
        cached: set[RequiredCheck] | None = None
        try:
            async with transaction() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT c.checks, c.updated_at FROM required_check_cache c "
                            "JOIN repository r ON r.id = c.repository_id "
                            "WHERE r.key = :repo AND c.branch = :branch"
                        ),
                        {"repo": self.repo.lower(), "branch": self.branch},
                    )
                ).first()
            if row is not None:
                cached = {
                    RequiredCheck(c.name, c.app_id) for c in CHECKS.validate_python(row.checks)
                }
                if datetime.now(UTC) - row.updated_at < CACHE_TTL:
                    return cached
            viewer = await User.for_session(None, login) if login else None
            await queue.schedule(
                REFRESH_JOB,
                {
                    "repo": self.repo.lower(),
                    "branch": self.branch,
                    "viewer_id": str(viewer.id) if viewer else None,
                },
                key=f"{REFRESH_JOB}:{self.repo.lower()}:{self.branch}",
                replace=False,
            )
        except Exception:
            logger.warning("Required-check cache read failed", exc_info=True)
        return cached

    async def refresh(self, viewer_id: UUID | None = None) -> None:
        owner, name = self.repo.split("/", 1)
        try:
            async with GitHubClient.as_app(owner, name) as github:
                required = await RequiredCheck.for_branch(
                    github.repo(owner, name), self.branch, include_review_checks=True
                )
        except GitHubAppUnavailable:
            logger.info(
                "Using viewer credentials for required-check refresh",
                extra={"repo_full_name": self.repo},
            )
            viewer = await User.get(viewer_id) if viewer_id else None
            if viewer is None:
                raise
            async with GitHubClient.as_user(viewer.github_login) as github:
                required = await RequiredCheck.for_branch(
                    github.repo(owner, name), self.branch, include_review_checks=True
                )
        if required is None:
            raise RuntimeError("Required checks unavailable")
        checks = json.dumps([{"name": check.name, "app_id": check.app_id} for check in required])
        async with session() as conn:
            repository_id = await Repository.ensure_id(conn, self.repo)
            await conn.execute(
                text(
                    "INSERT INTO required_check_cache (id, repository_id, branch, checks) "
                    "VALUES (:id, :repository_id, :branch, CAST(:checks AS jsonb)) "
                    "ON CONFLICT (repository_id, branch) DO UPDATE "
                    "SET checks = EXCLUDED.checks, updated_at = clock_timestamp(), updated_by = NULL"
                ),
                {
                    "id": uuid7(),
                    "repository_id": repository_id,
                    "branch": self.branch,
                    "checks": checks,
                },
            )
            await Topic.REQUIRED_CHECKS.invalidate(conn, key=self.repo.lower())


async def refresh_required_checks(job: Job) -> None:
    repo, branch = job.payload["repo"], job.payload["branch"]
    if not isinstance(repo, str) or not isinstance(branch, str):
        raise ValueError("Required-check refresh needs a repository and branch")
    viewer_id = job.payload.get("viewer_id")
    await RequiredCheckCache(repo, branch).refresh(
        UUID(viewer_id) if isinstance(viewer_id, str) else None
    )
