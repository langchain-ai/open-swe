"""Repository-scoped credentials for sandbox GitHub traffic."""

import logging
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from githubkit.auth import TokenAuthStrategy
from pydantic import BaseModel, PositiveInt

from agent.github import app
from agent.github.app import (
    PermissionKey,
    PermissionMap,
    get_github_app_installation_token_with_expiry,
    normalize_permissions,
)
from agent.github.sdk import GITHUB_API_VERSION, github_sdk
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

logger = logging.getLogger(__name__)

_REFRESH_MARGIN = timedelta(minutes=10)
_MAX_CACHED_THREADS = 512
type AccessScope = tuple[str, str, str, tuple[str, ...], PermissionKey]


class _InstallationRepository(BaseModel):
    id: PositiveInt
    full_name: str


@dataclass(frozen=True)
class SandboxGitHubAccess:
    token: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class _ThreadAccess:
    scope: AccessScope
    access: SandboxGitHubAccess
    expires_at: datetime


_THREAD_ACCESS: OrderedDict[str, _ThreadAccess] = OrderedDict()


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
    scope: AccessScope = (
        app.GITHUB_APP_ID,
        app.GITHUB_APP_INSTALLATION_ID,
        slug,
        tuple(sorted(allowed)),
        normalize_permissions(permissions),
    )
    if thread_id:
        cached = _THREAD_ACCESS.pop(thread_id, None)
        if (
            cached is not None
            and cached.scope == scope
            and cached.expires_at > datetime.now(UTC) + _REFRESH_MARGIN
        ):
            _THREAD_ACCESS[thread_id] = cached
            return cached.access

    access = await repository_token(sorted(allowed), permissions=permissions)
    if thread_id and access.token and access.expires_at:
        try:
            expires_at = datetime.fromisoformat(access.expires_at)
        except ValueError:
            logger.warning("Cannot cache GitHub access with invalid expiry", exc_info=True)
            return access
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at > datetime.now(UTC) + _REFRESH_MARGIN:
            _THREAD_ACCESS[thread_id] = _ThreadAccess(scope, access, expires_at)
            while len(_THREAD_ACCESS) > _MAX_CACHED_THREADS:
                _THREAD_ACCESS.popitem(last=False)
    return access
