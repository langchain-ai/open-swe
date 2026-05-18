"""Profile lookup + override helpers consumed by ``agent.server.get_agent``."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from langgraph_sdk import get_client

from .options import SUPPORTED_MODEL_IDS, model_supports_effort
from .profiles import PROFILES_NAMESPACE, get_login_for_email

logger = logging.getLogger(__name__)


async def resolve_login_from_email(email: str | None) -> str | None:
    """Look up the GitHub login linked to a verified email in the store."""
    if not isinstance(email, str) or not email.strip():
        return None
    return await get_login_for_email(email)


async def resolve_github_login(config: dict[str, Any]) -> str | None:
    """Best-effort resolution of the triggering user's GitHub login from config."""
    configurable = (config or {}).get("configurable") or {}

    login = configurable.get("github_login")
    if isinstance(login, str) and login.strip():
        return login.strip()

    slack_thread = configurable.get("slack_thread") or {}
    email = configurable.get("user_email") or slack_thread.get("triggering_user_email")
    return await resolve_login_from_email(email if isinstance(email, str) else None)


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


def normalize_profile_overrides(profile: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(model_id, reasoning_effort)`` if both are valid, else ``(None, None)``."""
    model_id = profile.get("default_model")
    effort = profile.get("reasoning_effort")
    if (
        isinstance(model_id, str)
        and model_id in SUPPORTED_MODEL_IDS
        and isinstance(effort, str)
        and model_supports_effort(model_id, effort)
    ):
        return model_id, effort
    return None, None
