"""Repository-scoped credentials for sandbox GitHub traffic."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

from githubkit.auth import TokenAuthStrategy
from pydantic import AwareDatetime, BaseModel, Field, PositiveInt

from agent.github import app
from agent.github.app import (
    PermissionMap,
    get_github_app_installation_token_with_expiry,
)
from agent.github.sdk import GITHUB_API_VERSION, github_sdk
from agent.store import TypedStore
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

logger = logging.getLogger(__name__)

# Reconcile missed webhooks independently of the one-hour token lifetime.
_REPOSITORY_RECONCILE_INTERVAL = timedelta(days=1)
_MISSING_REPOSITORY_RETRY_INTERVAL = timedelta(minutes=5)
type RepositoryScope = tuple[str, str, tuple[str, ...]]
type RepositoryId = Annotated[int, Field(strict=True, gt=0)]
REPOSITORY_DISCOVERY_EVENTS = frozenset({"installation", "installation_repositories", "repository"})


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
    generation: str
    refresh_after: AwareDatetime


class _RepositoryGeneration(BaseModel):
    generation: str


_WORKSPACE_REPOSITORIES = TypedStore(
    ["workspace_github_repositories", "v1"], _WorkspaceRepositories
)
_REPOSITORY_GENERATIONS = TypedStore(["github_repository_generations", "v1"], _RepositoryGeneration)


def _generation_key() -> str:
    return f"{app.GITHUB_APP_ID}:{app.GITHUB_APP_INSTALLATION_ID}"


async def invalidate_repository_discovery(event_type: str, payload: dict[str, object]) -> None:
    """Invalidate workspace mappings after a signature-verified GitHub event."""
    installation = payload.get("installation")
    if not isinstance(installation, dict):
        return
    if str(installation.get("id")) != app.GITHUB_APP_INSTALLATION_ID:
        return
    if event_type == "repository" and payload.get("action") not in {
        "created",
        "deleted",
        "renamed",
        "transferred",
        "privatized",
        "publicized",
    }:
        return
    # A generation prevents a discovery already in flight from undoing invalidation.
    await _REPOSITORY_GENERATIONS.put(
        _generation_key(), _RepositoryGeneration(generation=str(uuid4()))
    )


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


async def _read_workspace_repositories(slug: str) -> _WorkspaceRepositories | None:
    try:
        return await _WORKSPACE_REPOSITORIES.get(slug)
    except Exception:
        logger.warning("Workspace GitHub repository mapping read failed", exc_info=True)
        return None


async def _workspace_repositories(
    slug: str, allowed: set[str], *, refresh: bool = False
) -> tuple[dict[str, int], bool]:
    scope: RepositoryScope = (
        app.GITHUB_APP_ID,
        app.GITHUB_APP_INSTALLATION_ID,
        tuple(sorted(allowed)),
    )
    # Store failures are cache misses; only a successful GitHub lookup can replace them.
    try:
        cached, revision = await asyncio.gather(
            _read_workspace_repositories(slug), _REPOSITORY_GENERATIONS.get(_generation_key())
        )
    except Exception:
        logger.warning("Workspace GitHub repository mapping read failed", exc_info=True)
        return await _discover_repositories(allowed), False
    generation = revision.generation if revision is not None else ""
    if (
        not refresh
        and cached is not None
        and cached.scope == scope
        and cached.generation == generation
        and cached.refresh_after > datetime.now(UTC)
    ):
        return cached.repositories, True

    repositories = await _discover_repositories(allowed)
    interval = (
        _REPOSITORY_RECONCILE_INTERVAL
        if repositories.keys() == allowed
        else _MISSING_REPOSITORY_RETRY_INTERVAL
    )
    record = _WorkspaceRepositories(
        scope=scope,
        repositories=repositories,
        generation=generation,
        refresh_after=datetime.now(UTC) + interval,
    )
    try:
        await _WORKSPACE_REPOSITORIES.put(slug, record)
    except Exception:
        logger.warning("Workspace GitHub repository mapping write failed", exc_info=True)
    return repositories, False


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
    workspace_repos = {repo.lower() for repo in workspace.repos}
    allowed = workspace_repos.copy()
    if repositories is not None:
        allowed.intersection_update(repo.lower() for repo in repositories)
    if not allowed:
        return SandboxGitHubAccess()
    mapping, reused = await _workspace_repositories(slug, workspace_repos)
    repository_ids = sorted({repo_id for name, repo_id in mapping.items() if name in allowed})
    try:
        return await _repository_ids_token(repository_ids, permissions=permissions)
    except RuntimeError:
        if not reused:
            raise
        logger.warning("Cached workspace repository token failed; refreshing repository mapping")
    mapping, _ = await _workspace_repositories(slug, workspace_repos, refresh=True)
    repository_ids = sorted({repo_id for name, repo_id in mapping.items() if name in allowed})
    return await _repository_ids_token(repository_ids, permissions=permissions)
