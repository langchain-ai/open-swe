"""Repository-scoped credentials for sandbox GitHub traffic."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from githubkit.auth import TokenAuthStrategy
from pydantic import BaseModel, PositiveInt

from agent.database import postgres
from agent.github.app import (
    PermissionMap,
    get_github_app_installation_token_with_expiry,
)
from agent.github.repositories import Repository
from agent.github.sdk import GITHUB_API_VERSION, github_sdk
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

logger = logging.getLogger(__name__)

RECHECK_MISSING_AFTER = timedelta(minutes=10)


class _InstallationRepository(BaseModel):
    id: PositiveInt
    full_name: str


@dataclass(frozen=True)
class SandboxGitHubAccess:
    token: str | None = None
    expires_at: str | None = None


async def repository_token(
    repositories: Sequence[str], *, permissions: PermissionMap | None = None
) -> SandboxGitHubAccess:
    """Mint access only to full repository names available to the installation.

    Ids stored on ``repository`` rows skip the installation listing. It still
    runs when a name has no id and was not looked for within
    ``RECHECK_MISSING_AFTER``, and when GitHub refuses the stored ids. A missing
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
            repository_ids=stored_ids, permissions=permissions, log_errors=False
        )
        if token:
            return SandboxGitHubAccess(token, expires_at)
        logger.info("Stored repository ids were refused", extra={"repository_ids": stored_ids})
    repository_ids = await _listed_repository_ids(allowed, stored or {})
    if not repository_ids:
        return SandboxGitHubAccess()
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repository_ids=repository_ids, permissions=permissions
    )
    if not token:
        raise RuntimeError("Workspace GitHub repository token is unavailable")
    return SandboxGitHubAccess(token, expires_at)


async def _stored_repositories(allowed: set[str]) -> dict[str, Repository] | None:
    """The ``repository`` rows for ``allowed``, or ``None`` when they cannot be read."""
    if not postgres.configured():
        return None
    try:
        return await Repository.by_keys(allowed)
    except Exception:
        logger.warning("Stored repository ids are unavailable", exc_info=True)
        return None


def _ids_are_current(allowed: set[str], stored: Mapping[str, Repository]) -> bool:
    cutoff = datetime.now(UTC) - RECHECK_MISSING_AFTER
    for key in allowed:
        row = stored.get(key)
        if row is None:
            return False
        if row.github_id is None and (
            row.github_checked_at is None or row.github_checked_at < cutoff
        ):
            return False
    return True


async def _listed_repository_ids(allowed: set[str], stored: Mapping[str, Repository]) -> list[int]:
    """Look ``allowed`` up in the installation's listing and store what it found.

    A stored id the installation still reaches is kept even when its repository
    was renamed, so a repository created under the old name gains nothing.
    """
    discovery_token, _ = await get_github_app_installation_token_with_expiry()
    if not discovery_token:
        raise RuntimeError("GitHub App installation token is unavailable")
    reachable: set[int] = set()
    listed: dict[str, int] = {}
    async with github_sdk(TokenAuthStrategy(discovery_token)) as client:
        async for item in client.rest.paginate(
            client.rest(GITHUB_API_VERSION).apps.async_list_repos_accessible_to_installation,
            map_func=lambda response: response.json()["repositories"],
            per_page=100,
            headers={"X-GitHub-Api-Version": GITHUB_API_VERSION},
        ):
            repo = _InstallationRepository.model_validate(item)
            reachable.add(repo.id)
            if repo.full_name.lower() in allowed:
                listed[repo.full_name.lower()] = repo.id
    resolved: dict[str, int | None] = {}
    for key in allowed:
        row = stored.get(key)
        kept = row.github_id if row is not None and row.github_id in reachable else None
        resolved[key] = kept or listed.get(key)
    await _record_github_ids({key: repo_id for key, repo_id in resolved.items() if key in stored})
    return sorted({repo_id for repo_id in resolved.values() if repo_id is not None})


async def _record_github_ids(github_ids: Mapping[str, int | None]) -> None:
    if not github_ids:
        return
    try:
        await Repository.record_github_ids(github_ids, checked_at=datetime.now(UTC))
    except Exception:
        logger.warning("Could not store repository ids", exc_info=True)


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
