"""Startup report of the effective configuration and deprecated settings.

Only variable *names* are ever logged; values never leave the environment.
"""

import logging
import os
from collections.abc import Mapping

logger = logging.getLogger(__name__)

_DEPRECATIONS: tuple[tuple[str, str], ...] = (
    (
        "GITHUB_APP_ID",
        "GITHUB_APP_CLIENT_ID is the GitHub App JWT issuer; GITHUB_APP_ID is only used when "
        "GITHUB_APP_CLIENT_ID is unset.",
    ),
    ("SLACK_REPO_OWNER", "set the default repository in Admin → Team settings instead."),
    ("SLACK_REPO_NAME", "set the default repository in Admin → Team settings instead."),
    ("DEFAULT_REPO_OWNER", "set the default repository in Admin → Team settings instead."),
    ("DEFAULT_REPO_NAME", "set the default repository in Admin → Team settings instead."),
    (
        "LANGSMITH_TRACING_PROJECT_ID_PROD",
        "trace links resolve the open-swe-agent / open-swe-review projects by name; remove it.",
    ),
    (
        "LANGCHAIN_PROJECT",
        "graphs pin their own tracing projects; this variable has no effect on Open SWE.",
    ),
)

# Surface -> variables that must all be present for it to be enabled.
_SURFACES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LangSmith", ("LANGSMITH_API_KEY_PROD",)),
    ("GitHub", ("GITHUB_APP_CLIENT_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_WEBHOOK_SECRET")),
    ("Slack", ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET")),
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
    "LANGSMITH_TENANT_ID_PROD",
    "LANGSMITH_URL_PROD",
    "DEFAULT_SANDBOX_SNAPSHOT_ID",
)


def _is_set(env: Mapping[str, str], name: str) -> bool:
    return bool(env.get(name, "").strip())


def deprecated_env_warnings(env: Mapping[str, str]) -> list[str]:
    """Human-readable warnings for every deprecated variable present in ``env``."""
    return [f"{name} is deprecated: {hint}" for name, hint in _DEPRECATIONS if _is_set(env, name)]


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
