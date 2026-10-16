"""Instance-wide opt-in list of repos that Open SWE Review may auto-review.

Repos default to **disabled** — webhooks for repos absent from the list are
ignored, so an operator who installs the GitHub App into a new org doesn't get
surprise review comments on every PR.
"""

import logging

from sqlalchemy import select, update

from openswe.database import postgres
from openswe.github.repo_names import normalize_repo_full_name
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


async def set_review_repo_enabled(full_name: str, enabled: bool) -> list[str]:
    full_name = normalize_repo_full_name(full_name)
    async with postgres.session() as session:
        repository_id = await Repository.ensure_id(session, full_name)
        await session.execute(
            update(Repository).where(Repository.id == repository_id).values(review_enabled=enabled)
        )
    return await list_enabled_review_repos()


async def is_review_repo_enabled(owner: str, name: str) -> bool:
    """Whether auto-review is opted in for a repo.

    Fail-soft on purpose: this gates a GitHub webhook, and an unreachable
    database must read as "not opted in" (skip the review) rather than 500 the
    webhook.
    """
    if not owner or not name:
        logger.warning(
            "is_review_repo_enabled called with missing owner/name (owner=%r, name=%r)",
            owner,
            name,
        )
        return False
    try:
        repository = await Repository.get(f"{owner}/{name}")
    except Exception:
        logger.warning("enabled review repos lookup failed; skipping auto-review", exc_info=True)
        return False
    return repository is not None and repository.review_enabled is True
