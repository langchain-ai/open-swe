"""Team-wide opt-in list of repos that Open SWE Review may auto-review.

A single record keyed ``"default"`` holds the list. Repos default to
**disabled** — webhooks for repos absent from the list are ignored, so an
operator who installs the GitHub App into a new org doesn't get surprise
review comments on every PR.
"""

import logging
from datetime import UTC, datetime

from langgraph_sdk import get_client

from .review_styles import normalize_repo_full_name

logger = logging.getLogger(__name__)

ENABLED_REVIEW_REPOS_NAMESPACE: list[str] = ["enabled_review_repos"]
ENABLED_REVIEW_REPOS_KEY = "default"


def _client():
    return get_client()


async def list_enabled_review_repos() -> list[str]:
    try:
        item = await _client().store.get_item(
            ENABLED_REVIEW_REPOS_NAMESPACE, ENABLED_REVIEW_REPOS_KEY
        )
    except Exception:
        logger.exception(
            "enabled review repos lookup failed (namespace=%s, key=%s)",
            ENABLED_REVIEW_REPOS_NAMESPACE,
            ENABLED_REVIEW_REPOS_KEY,
        )
        return []
    if item is None:
        logger.info(
            "No enabled-review-repos store item found (namespace=%s, key=%s); "
            "treating as empty list",
            ENABLED_REVIEW_REPOS_NAMESPACE,
            ENABLED_REVIEW_REPOS_KEY,
        )
        return []
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    if not isinstance(value, dict):
        logger.warning("enabled-review-repos store item has unexpected shape: %r", item)
        return []
    repos = value.get("repos")
    if not isinstance(repos, list):
        logger.warning("enabled-review-repos value missing 'repos' list: %r", value)
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
    await _client().store.put_item(
        ENABLED_REVIEW_REPOS_NAMESPACE,
        ENABLED_REVIEW_REPOS_KEY,
        {"repos": repos, "updated_at": datetime.now(UTC).isoformat()},
    )
    return repos


async def is_review_repo_enabled(owner: str, name: str) -> bool:
    if not owner or not name:
        logger.warning(
            "is_review_repo_enabled called with missing owner/name (owner=%r, name=%r)",
            owner,
            name,
        )
        return False
    full_name = f"{owner.lower()}/{name.lower()}"
    enabled = await list_enabled_review_repos()
    result = any(r.lower() == full_name for r in enabled)
    logger.info(
        "Auto-review enabled check for %s: %s (enabled_repos=%s)",
        full_name,
        result,
        enabled,
    )
    return result
