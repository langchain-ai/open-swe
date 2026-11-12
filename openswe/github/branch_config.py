"""Repository branch configuration refreshed outside PR status requests."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import httpx2
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import text

from openswe.database.postgres import session, transaction
from openswe.github.ci import RequiredCheck
from openswe.github.http import GitHubAppUnavailable, GitHubClient, GraphQLError, RepoClient
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
class BranchRules:
    checks: set[RequiredCheck]
    requires_conversation_resolution: bool | None
    required_approving_reviews: int | None = None


@dataclass(frozen=True)
class BranchConfig:
    repo: str
    branch: str

    async def read(self, login: str | None = None) -> BranchRules | None:
        cached: BranchRules | None = None
        try:
            async with transaction() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT c.checks, c.requires_conversation_resolution, c.required_approving_reviews, c.conversation_rule_updated_at, c.updated_at FROM branch_config c "
                            "JOIN repository r ON r.id = c.repository_id "
                            "WHERE r.key = :repo AND c.branch = :branch"
                        ),
                        {"repo": self.repo.lower(), "branch": self.branch},
                    )
                ).first()
            if row is not None:
                cached = BranchRules(
                    {RequiredCheck(c.name, c.app_id) for c in CHECKS.validate_python(row.checks)},
                    row.requires_conversation_resolution,
                    row.required_approving_reviews,
                )
                if (
                    cached.requires_conversation_resolution is not None
                    and cached.required_approving_reviews is not None
                    and row.conversation_rule_updated_at is not None
                    and datetime.now(UTC) - row.conversation_rule_updated_at < CACHE_TTL
                    and datetime.now(UTC) - row.updated_at < CACHE_TTL
                ):
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
            logger.warning("Branch config read failed", exc_info=True)
        return cached

    async def refresh(self, viewer_id: UUID | None = None) -> None:
        owner, name = self.repo.split("/", 1)
        try:
            async with GitHubClient.as_app(owner, name) as github:
                repo = github.repo(owner, name)
                required = await RequiredCheck.for_branch(
                    repo, self.branch, include_review_checks=True
                )
                rules = await self._pull_request_rules(repo)
        except GitHubAppUnavailable:
            logger.info(
                "Using viewer credentials for required-check refresh",
                extra={"repo_full_name": self.repo},
            )
            viewer = await User.get(viewer_id) if viewer_id else None
            if viewer is None:
                raise
            async with GitHubClient.as_user(viewer.github_login) as github:
                repo = github.repo(owner, name)
                required = await RequiredCheck.for_branch(
                    repo, self.branch, include_review_checks=True
                )
                rules = await self._pull_request_rules(repo)
        if required is None:
            raise RuntimeError("Required checks unavailable")
        resolution_required, approvals, rule_error = rules
        checks = json.dumps([{"name": check.name, "app_id": check.app_id} for check in required])
        async with session() as conn:
            repository_id = await Repository.ensure_id(conn, self.repo)
            await conn.execute(
                text(
                    "INSERT INTO branch_config (id, repository_id, branch, checks, requires_conversation_resolution, required_approving_reviews, conversation_rule_updated_at) "
                    "VALUES (:id, :repository_id, :branch, CAST(:checks AS jsonb), :resolution_required, :approvals, CASE WHEN CAST(:resolution_required AS boolean) IS NOT NULL THEN clock_timestamp() END) "
                    "ON CONFLICT (repository_id, branch) DO UPDATE "
                    "SET checks = EXCLUDED.checks, requires_conversation_resolution = COALESCE(EXCLUDED.requires_conversation_resolution, branch_config.requires_conversation_resolution), required_approving_reviews = COALESCE(EXCLUDED.required_approving_reviews, branch_config.required_approving_reviews), conversation_rule_updated_at = COALESCE(EXCLUDED.conversation_rule_updated_at, branch_config.conversation_rule_updated_at), updated_at = clock_timestamp(), updated_by = NULL"
                ),
                {
                    "id": uuid7(),
                    "repository_id": repository_id,
                    "branch": self.branch,
                    "checks": checks,
                    "resolution_required": resolution_required,
                    "approvals": approvals,
                },
            )
            await Topic.REQUIRED_CHECKS.invalidate(conn, key=self.repo.lower())
        if rule_error is not None:
            raise rule_error

    async def _pull_request_rules(
        self, repo: RepoClient
    ) -> tuple[bool | None, int | None, Exception | None]:
        try:
            return (
                await repo.requires_conversation_resolution(self.branch),
                await repo.required_approving_reviews(self.branch),
                None,
            )
        except (httpx2.HTTPError, GraphQLError, ValueError) as exc:
            logger.warning("Pull request rule refresh failed", exc_info=True)
            return None, None, exc


async def refresh_required_checks(job: Job) -> None:
    repo, branch = job.payload["repo"], job.payload["branch"]
    if not isinstance(repo, str) or not isinstance(branch, str):
        raise ValueError("Required-check refresh needs a repository and branch")
    viewer_id = job.payload.get("viewer_id")
    await BranchConfig(repo, branch).refresh(
        UUID(viewer_id) if isinstance(viewer_id, str) else None
    )
