"""SCM provider helpers shared by clone, PR, and webhook routing."""

from __future__ import annotations

from typing import Any


def scm_provider(repo_config: dict[str, Any] | None) -> str:
    """Return normalized SCM provider name (default: github)."""
    if not repo_config:
        return "github"
    return str(repo_config.get("scm_provider") or "github").lower()


def is_azure_devops_repo(repo_config: dict[str, Any] | None) -> bool:
    return scm_provider(repo_config) == "azure_devops"


def azure_devops_repo_ready(repo_config: dict[str, Any] | None) -> bool:
    if not repo_config:
        return False
    owner = repo_config.get("owner") or repo_config.get("organization")
    project = repo_config.get("project")
    name = repo_config.get("name")
    return bool(owner and project and name)
