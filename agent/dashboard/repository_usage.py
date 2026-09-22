"""Per-user repository usage, independent of editable profile settings."""

import logging
from datetime import datetime
from typing import TypedDict

from sqlalchemy import text

from agent.database import postgres

logger = logging.getLogger(__name__)


class RepositoryUsage(TypedDict):
    repo: str
    use_count: int
    last_used_at: str


async def record_repository_usage(login: str, repo: str) -> None:
    if not postgres.configured():
        return
    try:
        async with postgres.transaction() as conn:
            await conn.execute(
                text("""
                    INSERT INTO user_repository_usage (login, repo)
                    VALUES (:login, :repo)
                    ON CONFLICT (login, repo) DO UPDATE SET
                        use_count = user_repository_usage.use_count + 1,
                        last_used_at = clock_timestamp()
                """),
                {"login": login.strip().lower(), "repo": repo.strip().lower()},
            )
    except Exception:
        logger.exception("Failed to record repository usage", extra={"login": login, "repo": repo})


async def get_repository_usage(login: str) -> list[RepositoryUsage]:
    if not postgres.configured():
        return []
    async with postgres.connection() as conn:
        rows = (
            await conn.execute(
                text("""
                    SELECT repo, use_count, last_used_at
                    FROM user_repository_usage
                    WHERE login = :login
                    ORDER BY last_used_at DESC, repo
                """),
                {"login": login.strip().lower()},
            )
        ).mappings()
        result: list[RepositoryUsage] = []
        for row in rows:
            last_used_at: datetime = row["last_used_at"]
            result.append(
                RepositoryUsage(
                    repo=row["repo"],
                    use_count=row["use_count"],
                    last_used_at=last_used_at.isoformat(),
                )
            )
        return result
