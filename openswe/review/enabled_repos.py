"""Instance-wide opt-in list of repos that Open SWE Review may auto-review.

Repos default to **disabled** — webhooks for repos absent from the list are
ignored, so an operator who installs the GitHub App into a new org doesn't get
surprise review comments on every PR.
"""

import logging

from sqlalchemy import select

from openswe.database import postgres
from openswe.github.repositories import Repository

logger = logging.getLogger(__name__)


async def list_enabled_review_repos() -> list[str]:
    async with postgres.session() as session:
        return list(
            await session.scalars(
                select(Repository.full_name)
                .where(Repository.review_enabled.is_(True))
                .order_by(Repository.key)
            )
        )


async def set_review_repo_enabled(
    full_name: str, enabled: bool, *, expected_version: str | None = None
) -> list[str]:
    await Repository.set_review_enabled(full_name, enabled, expected_version=expected_version)
    return await list_enabled_review_repos()


async def is_review_repo_enabled(owner: str, name: str) -> bool:
    """Whether auto-review is opted in for a repo.

    Fail-soft on purpose: this gates a GitHub webhook, and an unreachable
    database must read as "not opted in" (skip the review) rather than 500 the
    webhook.
    """
    if not owner or not name:
        return False
    try:
        repository = await Repository.get(f"{owner}/{name}")
    except Exception:
        logger.warning("enabled review repos lookup failed; skipping auto-review", exc_info=True)
        return False
    return repository is not None and repository.review_enabled is True
