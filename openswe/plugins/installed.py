"""An installed plugin acting for the person using it, within what they granted.

Plugin code never reaches the backend itself: the dashboard relays each call
from the plugin's sandboxed frame here. Every call is checked twice, against the
permissions the plugin's manifest was installed with and its repositories, and
against what the person may do themselves, so a plugin can never exceed the
person using it.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException

from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient, RepoClient
from openswe.github.repo_names import RepoFullName, normalize_repo_full_name
from openswe.plugins.manifest import PluginPermission
from openswe.plugins.records import DocumentScope, Installation, PluginDocuments, PluginThreads
from openswe.prompts import prompt
from openswe.threads.runs import start_dashboard_thread


class PluginPermissionDenied(PermissionError):
    def __init__(self, permission: PluginPermission) -> None:
        super().__init__(f"The plugin was not granted {permission.value}")


class RepositoryNotGranted(PermissionError):
    def __init__(self, repository: str) -> None:
        super().__init__(f"The plugin may not reach {repository}")


async def can_read_repository(login: str, repository: str) -> bool:
    """Whether ``login`` can read ``repository`` on GitHub now; an expired sign-in raises."""
    try:
        await require_repo_access_for_user(login, repository)
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            return False
        raise
    return True


@dataclass(frozen=True, slots=True)
class InstalledPlugin:
    installation: Installation
    login: str
    email: str | None
    user_id: UUID

    def require(self, *permissions: PluginPermission) -> None:
        """Pass when any of ``permissions`` was granted."""
        if not any(p in self.installation.manifest.permissions for p in permissions):
            raise PluginPermissionDenied(permissions[0])

    async def repository(self, raw: str) -> RepoFullName:
        """``raw`` as ``owner/repo``, when both the plugin and the person may reach it."""
        try:
            name = RepoFullName(normalize_repo_full_name(raw).lower())
        except ValueError as exc:
            raise RepositoryNotGranted(raw) from exc
        if name not in self.installation.repositories:
            raise RepositoryNotGranted(name)
        if not await can_read_repository(self.login, name):
            raise RepositoryNotGranted(name)
        return name

    async def document_scope(self, repository: str | None) -> DocumentScope:
        """Shared with readers of ``repository``, or private to this person without one."""
        self.require(PluginPermission.STORAGE)
        if repository is None:
            return DocumentScope(user_id=self.user_id)
        return DocumentScope(repository=await self.repository(repository))

    @property
    def documents(self) -> PluginDocuments:
        return PluginDocuments(self.installation.plugin)

    @asynccontextmanager
    async def github(self, repository: str, *, write: bool = False) -> AsyncIterator[RepoClient]:
        """``repository``'s client, acting with this person's own GitHub permissions."""
        if write:
            self.require(PluginPermission.GITHUB_WRITE)
        else:
            self.require(PluginPermission.GITHUB_READ, PluginPermission.GITHUB_WRITE)
        name = await self.repository(repository)
        owner, repo = name.split("/", 1)
        async with GitHubClient.as_user(self.login) as github:
            yield github.repo(owner, repo)

    @property
    def threads(self) -> PluginThreads:
        return PluginThreads(self.installation.id)

    async def start_thread(self, *, title: str, request: str, repository: str) -> str:
        """Start an agent thread the person approved; its prompt says the plugin wrote it."""
        self.require(PluginPermission.THREADS)
        name = await self.repository(repository)
        source = self.installation.source
        thread_id = await start_dashboard_thread(
            self.login,
            self.email,
            title=title,
            prompt=prompt(
                "plugins/thread-request",
                requester=self.login,
                plugin=self.installation.manifest.display_name,
                source=f"{source.repository}/{source.path}".rstrip("/"),
                repository=name,
                request=request,
            ),
            repos=[name],
            visibility="private",
        )
        await self.threads.record(thread_id, by=self.user_id)
        return thread_id
