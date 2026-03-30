"""Source control provider helpers."""

from __future__ import annotations

import os

from .github import create_github_pr, get_github_default_branch
from .gitlab import (
    create_gitlab_merge_request,
    get_gitlab_base_url,
    get_gitlab_default_branch,
    get_gitlab_host_url,
)

DEFAULT_SCM_PROVIDER = "github"
SUPPORTED_SCM_PROVIDERS = frozenset({"github", "gitlab"})


def get_scm_provider(repo_config: dict[str, str] | None = None) -> str:
    """Return the configured source control provider."""
    provider = (repo_config or {}).get("provider") or os.environ.get(
        "SCM_PROVIDER", DEFAULT_SCM_PROVIDER
    )
    normalized = provider.strip().lower()
    if normalized in SUPPORTED_SCM_PROVIDERS:
        return normalized
    return DEFAULT_SCM_PROVIDER


def get_clone_url(repo_owner: str, repo_name: str, repo_config: dict[str, str] | None = None) -> str:
    """Build the HTTPS clone URL for the selected source control provider."""
    provider = get_scm_provider(repo_config)
    if provider == "gitlab":
        base_url = get_gitlab_base_url()
        if not base_url:
            raise RuntimeError("GITLAB_URL is not configured")
        owner = repo_owner.strip("/")
        name = repo_name.strip("/")
        return f"{base_url.rstrip('/')}/{owner}/{name}.git"
    return f"https://github.com/{repo_owner}/{repo_name}.git"


def get_git_credential_username(provider: str) -> str:
    """Return the HTTPS username that should be used for git credentials."""
    return "oauth2" if provider == "gitlab" else "git"


def get_git_credential_host_url(provider: str) -> str:
    """Return the host URL used for storing git credentials."""
    if provider == "gitlab":
        return get_gitlab_host_url()
    return "https://github.com"


def get_review_request_label(provider: str) -> str:
    """Return a human-readable name for the provider's review request object."""
    return "merge request" if provider == "gitlab" else "pull request"


async def get_default_branch(
    provider: str,
    repo_owner: str,
    repo_name: str,
    token: str,
) -> str:
    """Get the default branch for the selected provider."""
    if provider == "gitlab":
        return await get_gitlab_default_branch(repo_owner, repo_name, token)
    return await get_github_default_branch(repo_owner, repo_name, token)


async def create_review_request(
    provider: str,
    repo_owner: str,
    repo_name: str,
    token: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
) -> tuple[str | None, int | None, bool]:
    """Create a PR or MR for the selected provider."""
    if provider == "gitlab":
        return await create_gitlab_merge_request(
            repo_owner=repo_owner,
            repo_name=repo_name,
            gitlab_token=token,
            title=title,
            head_branch=head_branch,
            base_branch=base_branch,
            body=body,
        )
    return await create_github_pr(
        repo_owner=repo_owner,
        repo_name=repo_name,
        github_token=token,
        title=title,
        head_branch=head_branch,
        base_branch=base_branch,
        body=body,
    )