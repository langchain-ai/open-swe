"""GitHub App execution authentication utilities."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langgraph.graph.state import RunnableConfig

from ..dashboard.team_settings import get_team_default_repo
from . import github_app
from .github_app import get_github_app_installation_token_with_expiry
from .github_token import cache_github_token_for_thread, invalidate_cached_github_token

logger = logging.getLogger(__name__)


def _repository_parts(repo: object) -> tuple[str, str] | None:
    if not isinstance(repo, Mapping):
        return None
    owner = repo.get("owner")
    name = repo.get("name")
    if isinstance(owner, str) and owner and isinstance(name, str) and name:
        return owner, name
    return None


async def resolve_trusted_repository(
    configurable: Mapping[str, Any],
    *,
    default_repo_resolver: Callable[[], Awaitable[dict[str, str] | None]] | None = None,
) -> tuple[str, str] | None:
    configured = _repository_parts(configurable.get("repo"))
    if configured is not None:
        return configured
    if configurable.get("repo_explicitly_none") is True:
        return None
    resolver = default_repo_resolver or get_team_default_repo
    return _repository_parts(await resolver())


async def repository_matches_configurable(
    configurable: Mapping[str, Any], owner: str, repo: str
) -> bool:
    trusted = await resolve_trusted_repository(configurable)
    return (
        trusted is not None
        and trusted[0].casefold() == owner.casefold()
        and trusted[1].casefold() == repo.casefold()
    )


async def _resolve_bot_installation_token(
    thread_id: str, owner: str, repository: str
) -> tuple[str, str | None]:
    token, expires_at = await get_github_app_installation_token_with_expiry(
        target_repo=f"{owner}/{repository}", repositories=[repository]
    )
    if not token:
        if not github_app.GITHUB_APP_ID or not github_app.GITHUB_APP_PRIVATE_KEY:
            raise RuntimeError(
                "GitHub App credentials are missing. Set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY."
            )
        target_repo = f"{owner}/{repository}"
        raise RuntimeError(
            f"No GitHub App installation covers repository {target_repo}. "
            "Check whether it was renamed or transferred and use its canonical owner."
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
    trusted = await resolve_trusted_repository(configurable)
    if trusted is None:
        raise RuntimeError(
            f"GitHub auth failed for thread {thread_id}: missing trusted repository context"
        )
    return await _resolve_bot_installation_token(thread_id, *trusted)


async def refresh_github_token(
    config: Mapping[str, Any] | RunnableConfig, thread_id: str
) -> str | None:
    """Mint a fresh installation token after the cached one expired mid-run.

    Installation tokens last an hour; a review that runs longer finds an empty
    cache when it publishes (MASTRA-417).
    """
    try:
        token, _expires_at = await resolve_github_token(config, thread_id)
    except RuntimeError:
        logger.warning("GitHub token refresh failed for thread %s", thread_id, exc_info=True)
        return None
    return token
