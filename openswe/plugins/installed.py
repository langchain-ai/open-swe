"""An installed plugin acting for the person using it, within what they granted.

Plugin code never reaches the backend itself: the dashboard relays each call
from the plugin's sandboxed frame here. Every call is checked twice: against the
permissions the plugin's manifest was installed with and the repositories this
person shared with it, and against what the person may do themselves, so a
plugin can never exceed the person using it.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from langgraph_sdk.errors import ConflictError

from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient, RepoClient
from openswe.github.repo_names import RepoFullName, normalize_repo_full_name
from openswe.openai_responses.client_tools import ClientToolSpec
from openswe.plugins.manifest import PluginPermission
from openswe.plugins.records import (
    DocumentScope,
    Installation,
    PluginChat,
    PluginChats,
    PluginDocuments,
    tool_prefix,
)
from openswe.plugins.rooms import Room
from openswe.thread_ids import plugin_chat_thread_id
from openswe.threads.runs import create_dashboard_thread_record, start_run_with_tool_results
from openswe.threads.summary import PLUGIN_CHAT_KEY
from openswe.utils.json_types import JsonObject


class PluginPermissionDenied(PermissionError):
    def __init__(self, permission: PluginPermission) -> None:
        super().__init__(f"The plugin was not granted {permission.value}")


MAX_AUTOMATIC_RESUMES = 20
"""Runs a plugin's tool results may start after one message from the person."""


@dataclass(frozen=True, slots=True)
class ChatAnswer:
    run_id: str | None
    """The run the answers started, if any."""
    paused: bool
    """Whether the agent stopped to wait for the person, its resumes spent."""


class PluginChatNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("chat not found")


class RepositoryNotGranted(PermissionError):
    def __init__(self, repository: str) -> None:
        super().__init__(f"{repository} is not shared with the plugin")


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
        """``raw`` as ``owner/repo``, when the person shared it with the plugin and can read it."""
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

    async def room(self, name: str, repository: str | None) -> Room:
        """A live room, shared like documents: per repository, or this person's own."""
        self.require(PluginPermission.REALTIME)
        scope = (
            DocumentScope(user_id=self.user_id)
            if repository is None
            else DocumentScope(repository=await self.repository(repository))
        )
        return Room(self.installation.plugin, scope, name)

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
    def chats(self) -> PluginChats:
        return PluginChats(self.installation.id, self.user_id)

    async def chat(self, name: str, repository: str | None) -> PluginChat:
        """The person's chat under ``name``, made with its agent thread the first time."""
        self.require(PluginPermission.AGENT)
        scope = None if repository is None else await self.repository(repository)
        if self.installation.preview_of is not None:
            # A preview chats in the thread building it, whatever the chat's name.
            thread = self.installation.preview_of
            return await self.chats.on_thread(thread) or await self.chats.record(
                name, scope, thread
            )
        found = await self.chats.get(name, scope)
        if found is not None:
            return found
        thread_id = plugin_chat_thread_id(str(self.installation.id), str(self.user_id), name, scope)
        owner, _, repo = (scope or "").partition("/")
        display = self.installation.manifest.display_name
        try:
            await create_dashboard_thread_record(
                thread_id,
                login=self.login,
                email=self.email,
                repo_config={"owner": owner, "name": repo} if scope else {},
                repo_explicitly_none=scope is None,
                prompt=None,
                title=f"{display}: {scope}" if scope else display,
                visibility="private",
                extra_metadata={PLUGIN_CHAT_KEY: str(self.installation.id)},
            )
        except ConflictError:
            # An earlier attempt made the thread and stopped before recording it.
            pass
        return await self.chats.record(name, scope, thread_id)

    async def own_chat(self, chat_id: UUID) -> PluginChat:
        """One of this person's chats beside the plugin."""
        self.require(PluginPermission.AGENT)
        chat = await self.chats.by_id(chat_id)
        if chat is None:
            raise PluginChatNotFound
        return chat

    def tool(self, name: str, description: str, parameters: JsonObject) -> ClientToolSpec:
        """One of the plugin's tools as the agent sees it, under the plugin's own prefix."""
        return ClientToolSpec(
            kind="function",
            name=f"{tool_prefix(self.installation.manifest.name)}{name}",
            description=description,
            parameters=parameters,
        )

    async def answer(self, chat: PluginChat, outputs: Mapping[str, str]) -> ChatAnswer:
        """Resume the chat with what the plugin's frame returned for its calls.

        The plugin's results resume it at most ``MAX_AUTOMATIC_RESUMES`` times
        after each message from the person; past that the agent waits for them.
        """
        answered = await chat.calls.answer(outputs, by=self.user_id)
        if not answered:
            return ChatAnswer(run_id=None, paused=False)
        if not await chat.calls.spend_resume(MAX_AUTOMATIC_RESUMES):
            # The results wait for the person's next message, which carries them.
            return ChatAnswer(run_id=None, paused=True)
        try:
            run_id = await start_run_with_tool_results(
                chat.thread_id,
                self.login,
                prompt="",
                tool_results=[ClientToolSpec.result_message(i, outputs[i]) for i in answered],
                overrides={},
                plugin_tools=True,
            )
        except Exception:
            # Nothing resumed, so the calls wait to be answered again.
            await chat.calls.reopen(answered)
            await chat.calls.refund_resume()
            raise
        await chat.calls.deliver(answered)
        return ChatAnswer(run_id=run_id, paused=False)
