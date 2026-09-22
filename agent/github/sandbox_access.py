"""Repository-scoped credentials for sandbox GitHub traffic."""

from collections.abc import Sequence
from dataclasses import dataclass

from githubkit.auth import TokenAuthStrategy
from pydantic import BaseModel, PositiveInt, StrictBool

from agent.github.app import (
    PermissionMap,
    get_github_app_installation_token_with_expiry,
)
from agent.github.sdk import GITHUB_API_VERSION, github_sdk
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES


class _InstallationRepository(BaseModel):
    id: PositiveInt
    full_name: str
    private: StrictBool | None = None


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
    installation = await _installation_repositories()
    return await _scoped_token(installation, allowed, permissions=permissions)


async def _installation_repositories() -> list[_InstallationRepository]:
    discovery_token, _ = await get_github_app_installation_token_with_expiry()
    if not discovery_token:
        raise RuntimeError("GitHub App installation token is unavailable")
    repositories: list[_InstallationRepository] = []
    async with github_sdk(TokenAuthStrategy(discovery_token)) as client:
        async for item in client.rest.paginate(
            client.rest(GITHUB_API_VERSION).apps.async_list_repos_accessible_to_installation,
            map_func=lambda response: response.json()["repositories"],
            per_page=100,
            headers={"X-GitHub-Api-Version": GITHUB_API_VERSION},
        ):
            repositories.append(_InstallationRepository.model_validate(item))
    return repositories


async def _scoped_token(
    installation: Sequence[_InstallationRepository],
    allowed: set[str],
    *,
    permissions: PermissionMap | None,
) -> SandboxGitHubAccess:
    repository_ids = {repo.id for repo in installation if repo.full_name.lower() in allowed}
    if not repository_ids:
        return SandboxGitHubAccess()
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repository_ids=sorted(repository_ids), permissions=permissions
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
    if workspace is None and slug != DEFAULT_WORKSPACE_SLUG:
        raise ValueError(f"Workspace {slug!r} does not exist")
    allowed = {repo.lower() for repo in workspace.repos} if workspace else set()
    if slug == DEFAULT_WORKSPACE_SLUG:
        installation = await _installation_repositories()
        allowed.update(
            await WORKSPACES.unassigned_repos(
                [repo.full_name for repo in installation if repo.private is True]
            )
        )
        if repositories is not None:
            allowed.intersection_update(repo.lower() for repo in repositories)
        return await _scoped_token(installation, allowed, permissions=permissions)
    if repositories is not None:
        allowed.intersection_update(repo.lower() for repo in repositories)
    return await repository_token(sorted(allowed), permissions=permissions)
