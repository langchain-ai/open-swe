"""Git provider abstraction for multi-platform support (GitHub, GitLab)."""

from __future__ import annotations

import logging
import os
from urllib.parse import quote as url_quote

logger = logging.getLogger(__name__)

# Supported providers
GITHUB = "github"
GITLAB = "gitlab"

_VALID_PROVIDERS = {GITHUB, GITLAB}


def get_git_provider() -> str:
    """Get the configured git provider from the GIT_PROVIDER env var.

    Defaults to 'github' for backward compatibility.
    """
    provider = os.environ.get("GIT_PROVIDER", GITHUB).lower()
    if provider not in _VALID_PROVIDERS:
        logger.warning("Unknown GIT_PROVIDER '%s', falling back to '%s'", provider, GITHUB)
        return GITHUB
    return provider


def get_gitlab_host() -> str:
    """Get the GitLab host from GITLAB_HOST env var. Defaults to 'gitlab.com'."""
    return os.environ.get("GITLAB_HOST", "gitlab.com")


def get_clone_url(owner: str, repo: str) -> str:
    """Build the clone URL for the configured provider."""
    provider = get_git_provider()
    if provider == GITLAB:
        host = get_gitlab_host()
        return f"https://{host}/{owner}/{repo}.git"
    return f"https://github.com/{owner}/{repo}.git"


def get_credential_url(token: str) -> str:
    """Build the git credential store line for the configured provider."""
    provider = get_git_provider()
    if provider == GITLAB:
        host = get_gitlab_host()
        return f"https://oauth2:{token}@{host}\n"
    return f"https://git:{token}@github.com\n"


def get_git_host() -> str:
    """Get the hostname for the configured provider."""
    provider = get_git_provider()
    if provider == GITLAB:
        return get_gitlab_host()
    return "github.com"


def get_noreply_email() -> str:
    """Get the noreply email for commit authoring."""
    provider = get_git_provider()
    if provider == GITLAB:
        host = get_gitlab_host()
        return f"open-swe@users.noreply.{host}"
    return "open-swe@users.noreply.github.com"


def get_gitlab_project_path(owner: str, repo: str) -> str:
    """Get the URL-encoded GitLab project path (owner%2Frepo)."""
    return url_quote(f"{owner}/{repo}", safe="")


def get_mr_or_pr_label() -> str:
    """Get 'Merge Request' or 'Pull Request' depending on provider."""
    provider = get_git_provider()
    if provider == GITLAB:
        return "Merge Request"
    return "Pull Request"
