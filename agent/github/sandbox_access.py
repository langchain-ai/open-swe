"""Repository-scoped credentials for sandbox GitHub traffic."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from githubkit.auth import TokenAuthStrategy
from pydantic import AwareDatetime, BaseModel, Field, PositiveInt

from agent.github import app
from agent.github.app import (
    PermissionMap,
    get_github_app_installation_token_with_expiry,
)
from agent.github.sdk import GITHUB_API_VERSION, github_sdk
from agent.store import TypedStore, put_value
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

logger = logging.getLogger(__name__)

_REPOSITORY_SCOPE_TTL = timedelta(minutes=50)
type RepositoryScope = tuple[str, str, str, tuple[str, ...]]


class _InstallationRepository(BaseModel):
    id: PositiveInt
    full_name: str


@dataclass(frozen=True)
class SandboxGitHubAccess:
    token: str | None = None
    expires_at: str | None = None


class _ThreadRepositories(BaseModel):
    scope: RepositoryScope
    repository_ids: tuple[Annotated[int, Field(strict=True, gt=0)], ...]
    expires_at: AwareDatetime


_THREAD_REPOSITORIES = TypedStore(["sandbox_github_repositories", "v1"], _ThreadRepositories)


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
    repository_ids = await _discover_repository_ids(allowed)
    return await _repository_ids_token(repository_ids, permissions=permissions)


async def _discover_repository_ids(allowed: set[str]) -> tuple[int, ...]:
    discovery_token, _ = await get_github_app_installation_token_with_expiry()
    if not discovery_token:
        raise RuntimeError("GitHub App installation token is unavailable")
    repository_ids: list[int] = []
    async with github_sdk(TokenAuthStrategy(discovery_token)) as client:
        async for item in client.rest.paginate(
            client.rest(GITHUB_API_VERSION).apps.async_list_repos_accessible_to_installation,
            map_func=lambda response: response.json()["repositories"],
            per_page=100,
            headers={"X-GitHub-Api-Version": GITHUB_API_VERSION},
        ):
            repo = _InstallationRepository.model_validate(item)
            if repo.full_name.lower() in allowed:
                repository_ids.append(repo.id)
    return tuple(sorted(set(repository_ids)))


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


async def _thread_repository_ids(
    thread_id: str, scope: RepositoryScope, allowed: set[str]
) -> tuple[int, ...]:
    # Store failures are cache misses; only a successful GitHub lookup can replace them.
    try:
        cached = await _THREAD_REPOSITORIES.get(thread_id)
    except Exception:
        logger.warning("GitHub repository scope cache read failed", exc_info=True)
        cached = None
    if cached is not None and cached.scope == scope and cached.expires_at > datetime.now(UTC):
        return cached.repository_ids

    repository_ids = await _discover_repository_ids(allowed)
    record = _ThreadRepositories(
        scope=scope,
        repository_ids=repository_ids,
        expires_at=datetime.now(UTC) + _REPOSITORY_SCOPE_TTL,
    )
    try:
        await put_value(
            _THREAD_REPOSITORIES.namespace,
            thread_id,
            record.model_dump(mode="json"),
            ttl=60,
        )
    except Exception:
        logger.warning("GitHub repository scope cache write failed", exc_info=True)
    return repository_ids


async def workspace_token(
    workspace_slug: str | None,
    *,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    thread_id: str | None = None,
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
    if not allowed:
        return SandboxGitHubAccess()
    if not thread_id:
        return await repository_token(sorted(allowed), permissions=permissions)
    scope: RepositoryScope = (
        app.GITHUB_APP_ID,
        app.GITHUB_APP_INSTALLATION_ID,
        slug,
        tuple(sorted(allowed)),
    )
    repository_ids = await _thread_repository_ids(thread_id, scope, allowed)
    return await _repository_ids_token(repository_ids, permissions=permissions)
