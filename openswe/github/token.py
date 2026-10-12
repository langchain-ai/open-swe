"""GitHub OAuth and LangSmith authentication utilities."""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from langgraph.graph.state import RunnableConfig
from langgraph_sdk import get_client

from openswe.config import ENV
from openswe.credential_scope import private_credential_login
from openswe.github.app import get_github_app_installation_token_with_expiry
from openswe.github.thread_token import (
    cache_github_token_for_thread,
    github_token_principal,
    invalidate_cached_github_token,
)
from openswe.run_config import RunConfig

logger = logging.getLogger(__name__)
_legacy_auth_impact_tasks: set[asyncio.Task[None]] = set()

client = get_client()


class GitHubUserAuthRequired(RuntimeError):
    """Raised when a mapped user has no valid GitHub OAuth token.

    Signals that the run cannot proceed on the user's behalf and that the user
    must (re-)authenticate. The Slack webhook blocks before creating a run, so
    this is a defense-in-depth signal at execution time.
    """

    def __init__(self, source: str, github_login: str | None) -> None:
        self.source = source
        self.github_login = github_login
        super().__init__(f"GitHub authentication required for {source} user '{github_login}'")


LANGSMITH_API_KEY = ENV.LANGSMITH_API_KEY.get()
X_SERVICE_AUTH_JWT_SECRET = ENV.X_SERVICE_AUTH_JWT_SECRET.get()
USER_ID_API_KEY_MAP = ENV.USER_ID_API_KEY_MAP.get()


def is_bot_token_only_mode() -> bool:
    """Check if we're in bot-token-only mode.

    This is the case when LANGSMITH_API_KEY is set (deployed) but neither
    X_SERVICE_AUTH_JWT_SECRET nor USER_ID_API_KEY_MAP is configured, meaning we
    can't resolve per-user GitHub OAuth tokens. In this mode the GitHub App
    installation token is used for all git operations instead.
    """
    return bool(LANGSMITH_API_KEY and not X_SERVICE_AUTH_JWT_SECRET and not USER_ID_API_KEY_MAP)


def _cache_resolved_github_token(
    thread_id: str,
    token: str,
    expires_at: str | None = None,
    *,
    principal: str | None = None,
    is_bot_token: bool = False,
) -> tuple[str, str | None]:
    cache_github_token_for_thread(
        thread_id,
        token,
        expires_at=expires_at,
        principal=principal,
        is_bot_token=is_bot_token,
    )
    return token, expires_at


async def _resolve_dashboard_user_token(
    thread_id: str, github_login: str
) -> tuple[str, str | None] | None:
    """Resolve a per-user GitHub token from the dashboard OAuth store."""
    login = github_login.strip()
    if not login:
        raise ValueError("missing github_login")

    from openswe.dashboard.profiles import get_oauth_token_record, get_valid_access_token

    token = await get_valid_access_token(login)
    if not token:
        return None
    record = await get_oauth_token_record(login)
    expires_at = record.get("token_expires_at") if isinstance(record, dict) else None
    return _cache_resolved_github_token(
        thread_id,
        token,
        expires_at=expires_at if isinstance(expires_at, str) else None,
        principal=github_token_principal(login=login),
    )


async def _resolve_bot_installation_token(thread_id: str) -> tuple[str, str | None]:
    """Get a GitHub App installation token and cache it for the thread."""
    bot_token, expires_at = await get_github_app_installation_token_with_expiry()
    if not bot_token:
        raise RuntimeError(
            "The GitHub App is not configured for workspace authentication. "
            "Set GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, and GITHUB_APP_INSTALLATION_ID."
        )
    logger.info("Using GitHub App installation token", extra={"thread_id": thread_id})
    return _cache_resolved_github_token(
        thread_id, bot_token, expires_at=expires_at, is_bot_token=True
    )


async def resolve_github_token(
    config: Mapping[str, Any] | RunnableConfig, thread_id: str
) -> tuple[str, str | None]:
    """Use workspace bot auth publicly and the verified owner's OAuth privately."""
    cfg = RunConfig.from_config(config)
    login = await private_credential_login(config, thread_id=thread_id)
    await invalidate_cached_github_token(thread_id)
    if login is None:
        return await _resolve_bot_installation_token(thread_id)
    user_token = await _resolve_dashboard_user_token(thread_id, login)
    if user_token is None:
        raise GitHubUserAuthRequired(cfg.source or "private", login)
    return user_token
