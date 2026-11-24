"""Repository-scoped credentials for sandbox GitHub traffic."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from githubkit.auth import TokenAuthStrategy
from pydantic import BaseModel, PositiveInt

from openswe.github.app import (
    PermissionMap,
    get_github_app_installation_token_with_expiry,
)
from openswe.github.repositories import Repository
from openswe.github.sdk import GITHUB_API_VERSION, github_sdk

logger = logging.getLogger(__name__)

RECHECK_MISSING_AFTER = timedelta(minutes=10)
# A stored id is looked up again after this long, so a name moves to the repository GitHub
# lists under it now within that time of a rename.
RECHECK_FOUND_AFTER = timedelta(hours=1)
# A sandbox keeps its token until the run's loop replaces it, 10 minutes before the
# expiry of the token the loop last put there. A token put there by anything else,
# such as a backend tool reconnecting to the sandbox, must outlive that schedule, so
# it is at most 10 minutes into its 1-hour life.
SANDBOX_TOKEN_MIN_LIFETIME = timedelta(minutes=50)

# Clone, fetch, read the pull request, and check CI. Nothing here can write.
READ_ONLY_REVIEW_PERMISSIONS: PermissionMap = {
    "actions": "read",
    "checks": "read",
    "contents": "read",
    "issues": "read",
    "metadata": "read",
    "pull_requests": "read",
    "statuses": "read",
}


class _InstallationRepository(BaseModel):
    id: PositiveInt
    full_name: str


@dataclass(frozen=True)
class SandboxGitHubAccess:
    token: str | None = None
    expires_at: str | None = None


async def repository_token(
    repositories: Sequence[str],
    *,
    permissions: PermissionMap | None = None,
    force_refresh: bool = False,
) -> SandboxGitHubAccess:
    """Mint access only to full repository names available to the installation.

    Ids stored on ``repository`` rows skip the installation listing. It still
    runs when a name has no row, has no id and was not looked for within
    ``RECHECK_MISSING_AFTER``, has an id not looked up within ``RECHECK_FOUND_AFTER``,
    and when GitHub refuses the stored ids. A missing
    match grants no credentials, so public repositories outside the installation
    remain readable anonymously. The installation-wide discovery token stays on
    the server and is never given to a sandbox.
    """
    allowed = {repo.lower() for repo in repositories}
    if not allowed:
        return SandboxGitHubAccess()
    stored = await _stored_repositories(allowed)
    if stored is not None and _ids_are_current(allowed, stored):
        stored_ids = sorted({row.github_id for row in stored.values() if row.github_id})
        if not stored_ids:
            return SandboxGitHubAccess()
        token, expires_at = await get_github_app_installation_token_with_expiry(
            repository_ids=stored_ids,
            permissions=permissions,
            log_errors=False,
            min_lifetime=SANDBOX_TOKEN_MIN_LIFETIME,
            force_refresh=force_refresh,
        )
        if token:
            return SandboxGitHubAccess(token, expires_at)
        logger.info("Stored repository ids were refused", extra={"repository_ids": stored_ids})
    repository_ids = await _listed_repository_ids(allowed)
    if not repository_ids:
        return SandboxGitHubAccess()
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repository_ids=repository_ids,
        permissions=permissions,
        min_lifetime=SANDBOX_TOKEN_MIN_LIFETIME,
        force_refresh=force_refresh,
    )
    if not token:
        raise RuntimeError("Sandbox GitHub repository token is unavailable")
    return SandboxGitHubAccess(token, expires_at)


async def _stored_repositories(allowed: set[str]) -> dict[str, Repository] | None:
    """The ``repository`` rows for ``allowed``, or ``None`` when they cannot be read."""
    try:
        return await Repository.by_keys(allowed)
    except Exception:
        logger.warning("Stored repository ids are unavailable", exc_info=True)
        return None


def _ids_are_current(allowed: set[str], stored: Mapping[str, Repository]) -> bool:
    now = datetime.now(UTC)
    for key in allowed:
        row = stored.get(key)
        if row is None or row.github_checked_at is None:
            return False
        recheck = RECHECK_MISSING_AFTER if row.github_id is None else RECHECK_FOUND_AFTER
        if row.github_checked_at < now - recheck:
            return False
    return True


async def _listed_repository_ids(allowed: set[str]) -> list[int]:
    """Look ``allowed`` up in the installation's listing and store what it found.

    A name resolves to the repository listed under it now, never to one it named before a
    rename: a thread's scope is the names it was given, and a repository created under a
    renamed one's old name must not reach the renamed one.
    """
    discovery_token, _ = await get_github_app_installation_token_with_expiry()
    if not discovery_token:
        raise RuntimeError("GitHub App installation token is unavailable")
    listed: dict[str, _InstallationRepository] = {}
    async with github_sdk(TokenAuthStrategy(discovery_token)) as client:
        async for item in client.rest.paginate(
            client.rest(GITHUB_API_VERSION).apps.async_list_repos_accessible_to_installation,
            map_func=lambda response: response.json()["repositories"],
            per_page=100,
            headers={"X-GitHub-Api-Version": GITHUB_API_VERSION},
        ):
            repo = _InstallationRepository.model_validate(item)
            if repo.full_name.lower() in allowed:
                listed[repo.full_name.lower()] = repo
    resolved = {key: repo.id if (repo := listed.get(key)) else None for key in allowed}
    await _record_github_ids(resolved, {key: repo.full_name for key, repo in listed.items()})
    return sorted({repo_id for repo_id in resolved.values() if repo_id is not None})


async def _record_github_ids(
    github_ids: Mapping[str, int | None], full_names: Mapping[str, str]
) -> None:
    if not github_ids:
        return
    try:
        await Repository.record_github_ids(
            github_ids, checked_at=datetime.now(UTC), full_names=full_names
        )
    except Exception:
        logger.warning("Could not store repository ids", exc_info=True)


async def installation_token(
    *, permissions: PermissionMap | None = None, force_refresh: bool = False
) -> SandboxGitHubAccess:
    """A token for every repository the GitHub App installation can reach."""
    token, expires_at = await get_github_app_installation_token_with_expiry(
        permissions=permissions,
        min_lifetime=SANDBOX_TOKEN_MIN_LIFETIME,
        force_refresh=force_refresh,
    )
    if not token:
        raise RuntimeError("GitHub installation token is unavailable")
    return SandboxGitHubAccess(token, expires_at)


async def sandbox_token(
    *,
    repositories: Sequence[str] | None = None,
    permissions: PermissionMap | None = None,
    force_refresh: bool = False,
) -> SandboxGitHubAccess:
    """Mint a sandbox token, preserving any explicit repository and permission limits."""
    if repositories is not None:
        return await repository_token(
            sorted(repositories), permissions=permissions, force_refresh=force_refresh
        )
    return await installation_token(permissions=permissions, force_refresh=force_refresh)
