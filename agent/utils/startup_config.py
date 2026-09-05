"""Startup report of the effective configuration and deprecated settings.

Only variable *names* are ever logged; values never leave the environment.
"""

import logging
import os
from collections.abc import Mapping

from agent.config import ENV

logger = logging.getLogger(__name__)

# Surface -> variables that must all be present for it to be enabled.
_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LangSmith", ("LANGSMITH_API_KEY",)),
    ("GitHub", ("GITHUB_APP_CLIENT_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_WEBHOOK_SECRET")),
    ("Slack", ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET")),
    ("Slack sign-in", ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET")),
    ("Linear", ("LINEAR_API_KEY", "LINEAR_WEBHOOK_SECRET")),
    (
        "Dashboard",
        (
            "GITHUB_APP_CLIENT_SECRET",
            "DASHBOARD_JWT_SECRET",
            "TOKEN_ENCRYPTION_KEY",
            "DASHBOARD_BASE_URL",
            "DASHBOARD_API_BASE_URL",
        ),
    ),
)

# Discovery-backed values: listed as overrides only when explicitly set.
_OVERRIDES: tuple[str, ...] = (
    "GITHUB_APP_INSTALLATION_ID",
    "SLACK_BOT_USER_ID",
    "SLACK_BOT_USERNAME",
    "DEFAULT_SANDBOX_SNAPSHOT_ID",
)


def _is_set(env: Mapping[str, str], name: str) -> bool:
    return ENV[name].is_set(env)


def deprecated_env_warnings(env: Mapping[str, str]) -> list[str]:
    """Human-readable warnings for every deprecated variable present in ``env``."""
    return [f"{name} is deprecated: {hint}" for name, hint in ENV.deprecated_in_use(env)]


def configuration_summary(env: Mapping[str, str]) -> list[str]:
    """One line per surface: enabled, disabled, or the variables still missing."""
    lines: list[str] = []
    for surface, names in _SURFACES:
        missing = [name for name in names if not _is_set(env, name)]
        if not missing:
            lines.append(f"{surface}: enabled")
        elif len(missing) == len(names):
            lines.append(f"{surface}: disabled")
        else:
            lines.append(f"{surface}: missing {', '.join(missing)}")
    overrides = [name for name in _OVERRIDES if _is_set(env, name)]
    if overrides:
        lines.append(f"Explicit overrides: {', '.join(overrides)}")
    return lines


def log_startup_configuration() -> None:
    for line in configuration_summary(os.environ):
        logger.info("config: %s", line)
    for warning in deprecated_env_warnings(os.environ):
        logger.warning("config: %s", warning)
