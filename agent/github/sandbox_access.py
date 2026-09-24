"""Repository-scoped credentials for sandbox GitHub traffic."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from githubkit.auth import TokenAuthStrategy
from pydantic import BaseModel, Field, PositiveInt

from agent.github import app
from agent.github.app import PermissionMap, get_github_app_installation_token_with_expiry
from agent.github.sdk import GITHUB_API_VERSION, github_sdk
from agent.store import TypedStore
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

logger = logging.getLogger(__name__)

type RepositoryScope = tuple[str, str, tuple[str, ...]]
type RepositoryId = Annotated[int, Field(strict=True, gt=0)]


class _InstallationRepository(BaseModel):
    id: PositiveInt
    full_name: str


@dataclass(frozen=True)
class SandboxGitHubAccess:
    token: str | None = None
    expires_at: str | None = None


class _WorkspaceRepositories(BaseModel):
    scope: RepositoryScope
    repositories: dict[str, RepositoryId]


_WORKSPACE_REPOSITORIES = TypedStore(
    ["workspace_github_repositories", "v1"], _WorkspaceRepositories
)


async def repository_token(
    repositories: Sequence[str], *, permissions: PermissionMap | None = None
) -> SandboxGitHubAccess:
    """Resolve current installation access for an explicit repository restriction.

    A missing match grants no credentials, so public repositories outside the
    installation remain readable anonymously. The installation-wide discovery
    token stays on the server and is never given to a sandbox.
    """
    allowed = {repo.lower() for repo in repositories}
    if not allowed:
        return SandboxGitHubAccess()
    mapping = await _discover_repositories(allowed)
    return await _repository_ids_token(sorted(set(mapping.values())), permissions=permissions)


async def _discover_repositories(allowed: set[str]) -> dict[str, int]:
    discovery_token, _ = await get_github_app_installation_token_with_expiry()
    if not discovery_token:
        raise RuntimeError("GitHub App installation token is unavailable")
    repositories: dict[str, int] = {}
    async with github_sdk(TokenAuthStrategy(discovery_token)) as client:
        async for item in client.rest.paginate(
            client.rest(GITHUB_API_VERSION).apps.async_list_repos_accessible_to_installation,
            map_func=lambda response: response.json()["repositories"],
            per_page=100,
            headers={"X-GitHub-Api-Version": GITHUB_API_VERSION},
        ):
            repo = _InstallationRepository.model_validate(item)
            if repo.full_name.lower() in allowed:
                repositories[repo.full_name.lower()] = repo.id
    return repositories


async def _repository_ids_token(
    repository_ids: Sequence[int], *, permissions: PermissionMap | None
) -> SandboxGitHubAccess:
    if not repository_ids:
        return SandboxGitHubAccess()
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repository_ids=repository_ids, permissions=permissions
    )
    if not token:
        raise RuntimeError("Workspace GitHub repository token is unavailable")
    return SandboxGitHubAccess(token, expires_at)


async def _workspace_repositories(slug: str, allowed: set[str]) -> dict[str, int]:
    scope: RepositoryScope = (
        app.GITHUB_APP_ID,
        app.GITHUB_APP_INSTALLATION_ID,
        tuple(sorted(allowed)),
    )
    # Repository IDs belong to the workspace; token expiry does not invalidate them.
    try:
        cached = await _WORKSPACE_REPOSITORIES.get(slug)
    except Exception:
        # Store outages must not prevent freshly resolved, scoped credentials.
        logger.warning("Workspace GitHub repository mapping read failed", exc_info=True)
        cached = None
    if cached is not None and cached.scope == scope:
        return cached.repositories

    repositories = await _discover_repositories(allowed)
    try:
        await _WORKSPACE_REPOSITORIES.put(
            slug, _WorkspaceRepositories(scope=scope, repositories=repositories)
        )
    except Exception:
        logger.warning("Workspace GitHub repository mapping write failed", exc_info=True)
    return repositories


async def _workspace_repo_names(slug: str) -> set[str]:
    workspace = await WORKSPACES.get(slug)
    if workspace is None:
        if slug != DEFAULT_WORKSPACE_SLUG:
            raise ValueError(f"Workspace {slug!r} does not exist")
        return set()
    return {repo.lower() for repo in workspace.repos}


async def workspace_token(
    workspace_slug: str | None, *, permissions: PermissionMap | None = None
) -> SandboxGitHubAccess:
    """Reuse regular coding access until the workspace repository configuration changes."""
    slug = workspace_slug or DEFAULT_WORKSPACE_SLUG
    allowed = await _workspace_repo_names(slug)
    if not allowed:
        return SandboxGitHubAccess()
    mapping = await _workspace_repositories(slug, allowed)
    repository_ids = sorted({repo_id for name, repo_id in mapping.items() if name in allowed})
    return await _repository_ids_token(repository_ids, permissions=permissions)


async def restricted_workspace_token(
    workspace_slug: str | None,
    repositories: Sequence[str],
    *,
    permissions: PermissionMap | None = None,
) -> SandboxGitHubAccess:
    """Resolve review/analyzer access separately, limited to current workspace membership."""
    allowed = await _workspace_repo_names(workspace_slug or DEFAULT_WORKSPACE_SLUG)
    allowed.intersection_update(repo.lower() for repo in repositories)
    return await repository_token(sorted(allowed), permissions=permissions)
