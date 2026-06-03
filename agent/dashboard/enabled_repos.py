"""Team-wide opt-in list of repos that Open SWE Review may auto-review.

A single record keyed ``"default"`` holds the list. Repos default to
**disabled** — webhooks for repos absent from the list are ignored, so an
operator who installs the GitHub App into a new org doesn't get surprise
review comments on every PR.
"""

from __future__ import annotations

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
    except Exception as e:
        logger.debug("enabled review repos lookup failed: %s", e)
        return []
    if item is None:
        return []
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    if not isinstance(value, dict):
        return []
    repos = value.get("repos")
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
    await _client().store.put_item(
        ENABLED_REVIEW_REPOS_NAMESPACE,
        ENABLED_REVIEW_REPOS_KEY,
        {"repos": repos, "updated_at": datetime.now(UTC).isoformat()},
    )
    return repos


async def is_review_repo_enabled(owner: str, name: str) -> bool:
    if not owner or not name:
        return False
    full_name = f"{owner.lower()}/{name.lower()}"
    enabled = await list_enabled_review_repos()
    return any(r.lower() == full_name for r in enabled)
