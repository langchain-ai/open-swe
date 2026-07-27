"""GitHub App execution authentication utilities."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langgraph.graph.state import RunnableConfig

from .github_app import get_github_app_installation_token_with_expiry
from .github_token import cache_github_token_for_thread, invalidate_cached_github_token

logger = logging.getLogger(__name__)


def _target_repo(configurable: Mapping[str, Any]) -> str | None:
    repo = configurable.get("repo")
    if not isinstance(repo, Mapping):
        return None
    owner = repo.get("owner")
    name = repo.get("name")
    if isinstance(owner, str) and owner and isinstance(name, str) and name:
        return f"{owner}/{name}"
    return None


async def _resolve_bot_installation_token(
    thread_id: str, target_repo: str | None = None
) -> tuple[str, str | None]:
    token, expires_at = await get_github_app_installation_token_with_expiry(target_repo=target_repo)
    if not token:
        raise RuntimeError(
            "GitHub App installation token unavailable. Set GITHUB_APP_ID, "
            "GITHUB_APP_PRIVATE_KEY, and GITHUB_APP_INSTALLATION_ID."
        )
    logger.info("Using GitHub App installation token for thread %s", thread_id)
    await invalidate_cached_github_token(thread_id)
    cache_github_token_for_thread(
        thread_id,
        token,
        expires_at=expires_at,
        is_bot_token=True,
    )
    return token, expires_at


async def resolve_github_token(
    config: Mapping[str, Any] | RunnableConfig, thread_id: str
) -> tuple[str, str | None]:
    """Resolve and cache the repository-scoped GitHub App installation token."""
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        raise RuntimeError(f"GitHub auth failed for thread {thread_id}: missing configurable state")
    if not configurable.get("source"):
        logger.error("Missing source for thread %s; cannot resolve GitHub auth", thread_id)
        raise RuntimeError(f"GitHub auth failed for thread {thread_id}: missing source")
    return await _resolve_bot_installation_token(thread_id, _target_repo(configurable))
