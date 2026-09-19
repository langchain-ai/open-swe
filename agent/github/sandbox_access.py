"""Repository-scoped credentials for sandbox GitHub traffic."""

from collections.abc import Sequence
from dataclasses import dataclass

import httpx2
from pydantic import BaseModel, PositiveInt

from agent.github.app import (
    PermissionMap,
    get_github_app_installation_token_with_expiry,
)
from agent.utils.http import DEFAULT_HTTP_TIMEOUT
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES


class _InstallationRepository(BaseModel):
    id: PositiveInt
    full_name: str


class _InstallationRepositories(BaseModel):
    repositories: list[_InstallationRepository]


@dataclass(frozen=True)
class SandboxGitHubAccess:
    token: str | None = None
    expires_at: str | None = None


async def repository_token(
    repositories: Sequence[str], *, permissions: PermissionMap | None = None
) -> SandboxGitHubAccess:
    """Mint access only to full repository names available to the installation.

    A missing match grants no credentials, so public repositories outside the
    installation remain readable anonymously. The installation-wide discovery
    token stays on the server and is never given to a sandbox.
    """
    allowed = {repo.lower() for repo in repositories}
    if not allowed:
        return SandboxGitHubAccess()
    discovery_token, _ = await get_github_app_installation_token_with_expiry()
    if not discovery_token:
        raise RuntimeError("GitHub App installation token is unavailable")
    repository_ids: list[int] = []
    async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        page = 1
        while True:
            response = await client.get(
                "https://api.github.com/installation/repositories",
                headers={
                    "Authorization": f"Bearer {discovery_token}",
                    "Accept": "application/vnd.github+json",
                },
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            batch = _InstallationRepositories.model_validate(response.json()).repositories
            for repo in batch:
                if repo.full_name.lower() in allowed:
                    repository_ids.append(repo.id)
            if len(batch) < 100:
                break
            page += 1
    if not repository_ids:
        return SandboxGitHubAccess()
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repository_ids=sorted(set(repository_ids)), permissions=permissions
    )
    if not token:
        raise RuntimeError("Workspace GitHub repository token is unavailable")
    return SandboxGitHubAccess(token, expires_at)


async def workspace_token(
    workspace_slug: str | None,
    *,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
) -> SandboxGitHubAccess:
    """Resolve permissions strictly; snapshot fallback must never broaden access."""
    slug = workspace_slug or DEFAULT_WORKSPACE_SLUG
    workspace = await WORKSPACES.get(slug)
    if workspace is None:
        if slug != DEFAULT_WORKSPACE_SLUG:
            raise ValueError(f"Workspace {slug!r} does not exist")
        return SandboxGitHubAccess()
    allowed = {repo.lower() for repo in workspace.repos}
    if repositories is not None:
        allowed.intersection_update(repo.lower() for repo in repositories)
    return await repository_token(sorted(allowed), permissions=permissions)
