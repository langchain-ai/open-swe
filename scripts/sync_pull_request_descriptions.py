"""Refresh stored PR titles/descriptions from GitHub using POSTGRES_URI and GitHub App auth."""

import asyncio
import logging
from uuid import UUID

from sqlalchemy import func, select, update

from openswe.database import postgres
from openswe.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from openswe.github.http import GITHUB_API_BASE, github_client, github_request
from openswe.github.pull_requests import PullRequest, PullRequestEvent

logger = logging.getLogger(__name__)


async def sync() -> int:
    postgres.require_configured()
    failures = 0
    last_id: UUID | None = None
    try:
        while True:
            async with postgres.session() as session:
                statement = select(PullRequest).order_by(PullRequest.id).limit(100)
                if last_id is not None:
                    statement = statement.where(PullRequest.id > last_id)
                rows = list(await session.scalars(statement))
            if not rows:
                break
            for row in rows:
                try:
                    installation = await get_github_app_installation_id_for_repo(
                        row.owner, row.repo
                    )
                    if installation is None:
                        raise RuntimeError(f"GitHub App cannot access {row.repo_full_name}")
                    token = await get_github_app_installation_token(installation_id=installation)
                    async with github_client(token=token) as client:
                        response = await github_request(
                            client,
                            "GET",
                            f"{GITHUB_API_BASE}/repos/{row.owner}/{row.repo}/pulls/{row.number}",
                        )
                        response.raise_for_status()
                        event = PullRequestEvent.model_validate(
                            {
                                "repository": {"full_name": row.repo_full_name},
                                "pull_request": response.json(),
                            }
                        )
                    refreshed = event.to_pull_request()
                    if refreshed is None or event.identity != (row.owner, row.repo, row.number):
                        raise ValueError(f"GitHub returned a different PR for {row.url}")
                    async with postgres.session() as session:
                        await session.execute(
                            update(PullRequest)
                            .where(
                                PullRequest.id == row.id,
                                PullRequest.title == row.title,
                                PullRequest.body == row.body,
                            )
                            .values(
                                title=refreshed.title,
                                body=refreshed.body,
                                updated_at=func.clock_timestamp(),
                            )
                        )
                except Exception:
                    failures += 1
                    logger.exception("PR sync failed", extra={"pr_url": row.url})
            last_id = rows[-1].id
    finally:
        await postgres.close()
    return 1 if failures else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(sync()))
