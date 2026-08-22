"""Team-wide opt-in list of repos that Open SWE Review may auto-review.

A single record keyed ``"default"`` holds the list. Repos default to
**disabled** — webhooks for repos absent from the list are ignored, so an
operator who installs the GitHub App into a new org doesn't get surprise
review comments on every PR.
"""

import logging

from ..store import get_value, now_iso, put_value
from .review_styles import normalize_repo_full_name

logger = logging.getLogger(__name__)

ENABLED_REVIEW_REPOS_NAMESPACE: list[str] = ["enabled_review_repos"]
ENABLED_REVIEW_REPOS_KEY = "default"


async def list_enabled_review_repos() -> list[str]:
    record = await get_value(ENABLED_REVIEW_REPOS_NAMESPACE, ENABLED_REVIEW_REPOS_KEY)
    repos = record.get("repos") if record else None
    if not isinstance(repos, list):
        return []
    return [r for r in repos if isinstance(r, str)]


async def set_review_repo_enabled(full_name: str, enabled: bool) -> list[str]:
    full_name = normalize_repo_full_name(full_name)
    current = set(await list_enabled_review_repos())
    if enabled:
        current.add(full_name)
    else:
        current.discard(full_name)
    repos = sorted(current)
    await put_value(
        ENABLED_REVIEW_REPOS_NAMESPACE,
        ENABLED_REVIEW_REPOS_KEY,
        {"repos": repos, "updated_at": now_iso()},
    )
    return repos


async def is_review_repo_enabled(owner: str, name: str) -> bool:
    """Whether auto-review is opted in for a repo.

    Fail-soft on purpose: this gates a GitHub webhook, and an unreachable store
    must read as "not opted in" (skip the review) rather than 500 the webhook.
    """
    if not owner or not name:
        return False
    full_name = f"{owner.lower()}/{name.lower()}"
    try:
        enabled = await list_enabled_review_repos()
    except Exception:
        logger.warning("enabled review repos lookup failed; skipping auto-review", exc_info=True)
        return False
    return any(r.lower() == full_name for r in enabled)
