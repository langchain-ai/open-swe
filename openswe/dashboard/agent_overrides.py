"""Profile lookup + override helpers consumed by ``openswe.server.get_agent``."""

import logging
from typing import Any

from openswe.dashboard.options import SUPPORTED_MODEL_IDS
from openswe.dashboard.profiles import PROFILES
from openswe.dashboard.workspace_settings import get_workspace_settings
from openswe.users import User
from openswe.users.repository_defaults import RepositoryDefaults

logger = logging.getLogger(__name__)


async def resolve_github_login(config: dict[str, Any]) -> str | None:
    """Best-effort resolution of the triggering user's GitHub login from config."""
    configurable = (config or {}).get("configurable") or {}

    if configurable.get("auth_provider") == "langsmith":
        user_id = configurable.get("user_id")
        if isinstance(user_id, str):
            canonical_id = User.key_id(f"user:{user_id}")
            return f"user:{canonical_id}" if canonical_id is not None else None
        return None

    login = configurable.get("github_login")
    if isinstance(login, str) and login.strip():
        return login.strip()

    slack_thread = configurable.get("slack_thread") or {}
    email = configurable.get("user_email") or slack_thread.get("triggering_user_email")
    return await User.login_for_email(email if isinstance(email, str) else None)


async def get_profile_default_repo(
    login: str | None, workspace: str | None = None, *, fallback_global: bool = True
) -> dict[str, str] | None:
    """Return the user's effective default repository for a selected workspace."""
    if not login:
        return None
    profile = await load_profile(login)
    if not profile:
        return None
    return RepositoryDefaults.model_validate(profile).repo_config(
        workspace, fallback_global=fallback_global
    )


async def load_profile(login: str) -> dict[str, Any] | None:
    """The user's profile record, or ``None`` when it is missing or unreadable.

    Fail-soft on purpose: every caller — ``openswe.server.get_agent``,
    :func:`get_profile_default_repo`, :func:`resolve_agent_model_id` — is on the
    path that starts an agent run, and a store blip must cost the run its
    per-user overrides, not the run itself. Dashboard reads that should surface
    a failure use :func:`openswe.dashboard.profiles.get_profile` instead.
    """
    try:
        return await PROFILES.get(login)
    except Exception:
        logger.warning("profile lookup failed for %s", login, exc_info=True)
        return None


def profile_draft_prs(profile: dict[str, Any] | None) -> bool:
    """Return whether new PRs should be drafts. Defaults to True."""
    value = profile.get("draft_prs") if isinstance(profile, dict) else None
    return value if isinstance(value, bool) else True


async def resolve_agent_model_id(
    github_login: str | None,
    per_thread_model_id: str | None = None,
    workspace: str | None = None,
) -> str:
    """Resolve the agent model ID using the same precedence as ``get_agent``.

    Order: per-thread override → the workspace's default.

    ``workspace`` is the workspace the run will land in, whose default
    applies; omitting it reads ``default``'s, which is only right for a run
    that lands there.
    """
    model_id, _effort = (await get_workspace_settings(workspace)).default_model("agent")
    if isinstance(per_thread_model_id, str) and per_thread_model_id in SUPPORTED_MODEL_IDS:
        model_id = per_thread_model_id
    return model_id
