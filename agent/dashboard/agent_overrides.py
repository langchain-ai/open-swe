"""Profile lookup + override helpers consumed by ``agent.server.get_agent``."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from langgraph_sdk import get_client

from ..utils.github_user_email_map import GITHUB_USER_EMAIL_MAP
from .options import SUPPORTED_MODEL_IDS, model_supports_effort
from .profiles import PROFILES_NAMESPACE

logger = logging.getLogger(__name__)


def resolve_github_login(config: dict[str, Any]) -> str | None:
    """Best-effort resolution of the triggering user's GitHub login from config."""
    configurable = (config or {}).get("configurable") or {}

    login = configurable.get("github_login")
    if isinstance(login, str) and login.strip():
        return login.strip()

    slack_thread = configurable.get("slack_thread") or {}
    email = configurable.get("user_email") or slack_thread.get("triggering_user_email")
    if isinstance(email, str) and email.strip():
        normalized = email.strip().lower()
        for gh_login, mapped in GITHUB_USER_EMAIL_MAP.items():
            if mapped.lower() == normalized:
                return gh_login
    return None


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
