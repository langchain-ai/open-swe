"""Repository-scoped credentials for sandbox GitHub traffic."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic

from githubkit.auth import TokenAuthStrategy
from pydantic import BaseModel, PositiveInt

from agent.github.app import (
    PermissionMap,
    get_github_app_installation_token_with_expiry,
)
from agent.github.sdk import GITHUB_API_VERSION, github_sdk
from agent.utils import ttl_cache
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

_INDEX_KEY = "github:installation-repositories"
_INDEX_TTL_SECONDS = 300
# GitHub keeps minting an id after its repository is renamed and the name reused,
# so an index that has not been refreshed stops being used at this age.
_INDEX_MAX_AGE_SECONDS = 1800


class _InstallationRepository(BaseModel):
    id: PositiveInt
    full_name: str


@dataclass(frozen=True)
class _RepositoryIndex:
    """Lowercased full name -> id for every repository the installation reaches."""

    ids: Mapping[str, int]
    listed_at: float

    def misses(self, repositories: Iterable[str]) -> bool:
        """Whether a newer listing could add one of ``repositories``.

        An installation only reaches its own account's repositories, so a name
        under another owner is settled however old the index is.
        """
        owners = {name.partition("/")[0] for name in self.ids}
        return any(
            repo not in self.ids and (not owners or repo.partition("/")[0] in owners)
            for repo in repositories
        )


@dataclass(frozen=True)
class SandboxGitHubAccess:
    token: str | None = None
    expires_at: str | None = None


async def _list_installation_repositories() -> _RepositoryIndex:
    listed_at = monotonic()
    discovery_token, _ = await get_github_app_installation_token_with_expiry()
    if not discovery_token:
        raise RuntimeError("GitHub App installation token is unavailable")
    ids: dict[str, int] = {}
    async with github_sdk(TokenAuthStrategy(discovery_token)) as client:
        async for item in client.rest.paginate(
            client.rest(GITHUB_API_VERSION).apps.async_list_repos_accessible_to_installation,
            map_func=lambda response: response.json()["repositories"],
            per_page=100,
            headers={"X-GitHub-Api-Version": GITHUB_API_VERSION},
        ):
            repo = _InstallationRepository.model_validate(item)
            ids[repo.full_name.lower()] = repo.id
    return _RepositoryIndex(ids, listed_at)


async def _cached_index() -> _RepositoryIndex:
    return await ttl_cache.cached_stale_while_revalidate(
        _INDEX_KEY, _INDEX_TTL_SECONDS, _list_installation_repositories
    )


async def _scoped_access(
    index: _RepositoryIndex,
    allowed: Iterable[str],
    permissions: PermissionMap | None,
    *,
    log_errors: bool = True,
) -> SandboxGitHubAccess | None:
    """Mint for the ``allowed`` names ``index`` resolves; ``None`` if GitHub refuses."""
    repository_ids = sorted({index.ids[repo] for repo in allowed if repo in index.ids})
    if not repository_ids:
        return SandboxGitHubAccess()
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repository_ids=repository_ids, permissions=permissions, log_errors=log_errors
    )
    return SandboxGitHubAccess(token, expires_at) if token else None


async def repository_token(
    repositories: Sequence[str], *, permissions: PermissionMap | None = None
) -> SandboxGitHubAccess:
    """Mint access only to full repository names available to the installation.

    A missing match grants no credentials, so public repositories outside the
    installation remain readable anonymously. The installation-wide discovery
    token stays on the server and is never given to a sandbox.

    Runs share the installation's listing, which refreshes in the background. A
    run lists for itself when that listing is past its maximum age, lacks a
    requested name, or holds ids GitHub refuses; only the cache's own serialized
    loads replace the shared listing, so an older one never overwrites a newer.
    """
    allowed = {repo.lower() for repo in repositories}
    if not allowed:
        return SandboxGitHubAccess()
    started = monotonic()
    index = await _cached_index()
    if started - index.listed_at > _INDEX_MAX_AGE_SECONDS or (
        index.listed_at < started and index.misses(allowed)
    ):
        index = await _list_installation_repositories()
    cached = index.listed_at < started
    access = await _scoped_access(index, allowed, permissions, log_errors=not cached)
    if access is None and cached:
        # GitHub refuses the whole token once any id has left the installation.
        index = await _list_installation_repositories()
        access = await _scoped_access(index, allowed, permissions)
    if access is None:
        raise RuntimeError("Workspace GitHub repository token is unavailable")
    return access


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
