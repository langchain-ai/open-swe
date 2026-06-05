"""Profile lookup + override helpers consumed by ``agent.server.get_agent``."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from langgraph_sdk import get_client

from .options import SUPPORTED_MODEL_IDS, model_supports_effort, provider_fallback_pair
from .profiles import PROFILES_NAMESPACE
from .user_mappings import cached_login_for_email, login_for_email

logger = logging.getLogger(__name__)


def resolve_login_from_email(email: str | None) -> str | None:
    """Reverse-lookup the user-mapping store for the GitHub login of an email.

    Reads the in-process mapping cache (sync). When the cache is cold the
    lookup misses; the webhook path that triggers a run primes the cache via
    :func:`agent.dashboard.user_mappings.refresh_cache` beforehand.
    """
    return cached_login_for_email(email)


async def resolve_login_from_email_async(email: str | None) -> str | None:
    """Async reverse-lookup that falls through to the Store on a cold cache.

    Use this from webhook/repo-resolution paths that may run on a freshly
    started worker before the user-mapping cache has been primed, so a mapped
    user still resolves to their GitHub login (and dashboard ``default_repo``).
    """
    return await login_for_email(email if isinstance(email, str) else None)


def resolve_github_login(config: dict[str, Any]) -> str | None:
    """Best-effort resolution of the triggering user's GitHub login from config."""
    configurable = (config or {}).get("configurable") or {}

    login = configurable.get("github_login")
    if isinstance(login, str) and login.strip():
        return login.strip()

    slack_thread = configurable.get("slack_thread") or {}
    email = configurable.get("user_email") or slack_thread.get("triggering_user_email")
    return resolve_login_from_email(email if isinstance(email, str) else None)


async def get_profile_default_repo(login: str | None) -> dict[str, str] | None:
    """Return ``{"owner", "name"}`` for the user's profile default_repo, if set."""
    if not login:
        return None
    profile = await load_profile(login)
    if not profile:
        return None
    default_repo = profile.get("default_repo")
    if not isinstance(default_repo, str):
        return None
    parts = default_repo.strip().split("/", 1)
    if len(parts) != 2:
        return None
    owner, name = parts[0].strip(), parts[1].strip()
    if not owner or not name:
        return None
    return {"owner": owner, "name": name}


async def load_profile(login: str) -> dict[str, Any] | None:
    try:
        item = await get_client().store.get_item(PROFILES_NAMESPACE, login)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        logger.warning("profile lookup failed for %s: %s", login, e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def profile_create_prs(profile: dict[str, Any] | None) -> bool:
    """Return whether the agent should always open a PR. Defaults to False."""
    if not isinstance(profile, dict):
        return False
    value = profile.get("create_prs")
    if isinstance(value, bool):
        return value
    return False


def _normalize_profile_model_pair(
    profile: dict[str, Any],
    *,
    model_key: str,
    effort_key: str,
) -> tuple[str | None, str | None]:
    model_id = profile.get(model_key)
    effort = profile.get(effort_key)
    if (
        isinstance(model_id, str)
        and model_id in SUPPORTED_MODEL_IDS
        and isinstance(effort, str)
        and model_supports_effort(model_id, effort)
    ):
        return model_id, effort
    # A stored selection whose exact id dropped out of the supported set (e.g. an
    # Opus minor-version bump) stays on its provider rather than being discarded
    # and silently deferring to the team default. An absent/unknown-provider
    # selection still returns (None, None) so the team default applies.
    if isinstance(model_id, str):
        provider_pair = provider_fallback_pair(model_id, effort)
        if provider_pair is not None:
            return provider_pair
    return None, None


def normalize_profile_overrides(profile: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(model_id, reasoning_effort)`` if both are valid, else ``(None, None)``."""
    return _normalize_profile_model_pair(
        profile,
        model_key="default_model",
        effort_key="reasoning_effort",
    )


def normalize_profile_subagent_overrides(
    profile: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Return the profile's subagent model pair if valid, else ``(None, None)``."""
    return _normalize_profile_model_pair(
        profile,
        model_key="default_subagent_model",
        effort_key="subagent_reasoning_effort",
    )
