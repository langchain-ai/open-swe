"""PostgreSQL regressions for what keeps apps inside the access they were given."""

import asyncio
import json
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from openswe.database import postgres
from openswe.openai_responses.client_tools import ClientToolSpec
from openswe.plugins import installed as installed_module
from openswe.plugins import records as records_module
from openswe.plugins.installed import InstalledPlugin, PluginPermissionDenied, RepositoryNotGranted
from openswe.plugins.manifest import PluginPermission
from openswe.plugins.records import (
    AppNotFound,
    Cursor,
    DocumentQuery,
    DocumentScope,
    Installation,
    InstallationNotFound,
    PluginApps,
    PluginChat,
    PluginChats,
    PluginDocuments,
    PluginInstallations,
    PluginNameTaken,
)
from openswe.plugins.rooms import Room
from openswe.ui_invalidations.topics import _readable_plugin_documents
from openswe.users import User

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada,bob")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


def _bundle(
    name: str = "docs", permissions: list[str] | None = None, body: str = "<p>docs</p>"
) -> str:
    manifest = {
        "name": name,
        "displayName": "Docs",
        "version": "1.0.0",
        "sidebar": {"label": "Docs"},
        "permissions": permissions if permissions is not None else ["storage"],
    }
    return f'<script type="application/openswe-plugin+json">{json.dumps(manifest)}</script>{body}'


async def _install(owner: UUID, html: str | None = None) -> UUID:
    """Publish ``html`` as ``owner``'s app and add it to their web UI."""
    version = await PluginApps.publish(owner, html or _bundle(), origin={})
    return await PluginInstallations.install(version, user_id=owner)


async def _person(login: str, external_id: str) -> UUID:
    return (await User.sign_in("github", external_id, login=login)).id


async def test_documents_stay_in_their_plugin_and_scope() -> None:
    ada, bob = await _person("ada", "1"), await _person("bob", "2")
    first = await _install(ada)
    # The same name published by someone else is another app with its own data.
    impostor = await _install(bob)
    docs = PluginDocuments((await PluginInstallations.get(first, ada)).plugin)
    other = PluginDocuments((await PluginInstallations.get(impostor, bob)).plugin)
    shared = DocumentScope(repository="acme/app")

    await docs.put("comments", shared, "c1", {"body": "shared"}, by=ada)
    await docs.put("notes", DocumentScope(user_id=ada), "n1", {"body": "ada only"}, by=ada)

    assert await other.get("comments", shared, "c1") is None
    assert await docs.get("comments", DocumentScope(repository="acme/web"), "c1") is None
    assert await docs.get("notes", DocumentScope(user_id=bob), "n1") is None
    assert [
        d.key for d in await docs.query("comments", shared, DocumentQuery(where={"body": "shared"}))
    ] == ["c1"]


async def test_documents_come_back_ordered_and_bounded() -> None:
    ada = await _person("ada", "1")
    installation = await _install(ada)
    docs = PluginDocuments((await PluginInstallations.get(installation, ada)).plugin)
    mine = DocumentScope(user_id=ada)
    for key, document in {
        "t-b": {"at": {"rank": 10}},
        "t-a": {"at": {"rank": 10}},
        "t-Z": {"at": {"rank": 2}},
        "t-c": {"at": {"rank": "first"}},
        "t-d": {"at": {"rank": True}},
        "t-e": {"at": {}},
        "u-a": {"at": {"rank": 1}},
    }.items():
        await docs.put("tasks", mine, key, document, by=ada)

    async def keys(query: DocumentQuery) -> list[str]:
        return [d.key for d in await docs.query("tasks", mine, query)]

    # Keys compare byte by byte, so uppercase sorts before lowercase.
    assert await keys(DocumentQuery(prefix="t-")) == ["t-Z", "t-a", "t-b", "t-c", "t-d", "t-e"]
    assert await keys(DocumentQuery(start=Cursor("t-a", inclusive=False), end=Cursor("t-d"))) == [
        "t-b",
        "t-c",
        "t-d",
    ]
    # Missing first, then booleans, numbers by value (2 before 10) and strings; ties by key.
    by_rank = DocumentQuery(prefix="t-", order_by=("at", "rank"))
    assert await keys(by_rank) == ["t-e", "t-d", "t-Z", "t-a", "t-b", "t-c"]
    # Paging resumes after the last document seen, ties included, in either direction.
    page = DocumentQuery(prefix="t-", order_by=("at", "rank"), limit=4)
    assert await keys(page) == ["t-e", "t-d", "t-Z", "t-a"]
    resumed = DocumentQuery(
        prefix="t-", order_by=("at", "rank"), start=Cursor(10, "t-a", inclusive=False)
    )
    assert await keys(resumed) == ["t-b", "t-c"]
    newest = DocumentQuery(
        prefix="t-", order_by=("at", "rank"), descending=True, start=Cursor(10), end=Cursor(2)
    )
    assert await keys(newest) == ["t-b", "t-a", "t-Z"]


async def test_a_chat_is_its_persons_and_each_call_is_answered_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ada, bob = await _person("ada", "1"), await _person("bob", "2")
    installation = await _install(ada, _bundle(permissions=["agent"]))
    await PluginInstallations.share(installation, ada, ["acme/app"])

    async def readable(_login: str, _repository: str) -> bool:
        return True

    monkeypatch.setattr(installed_module, "can_read_repository", readable)
    mine, theirs = PluginChats(installation, ada), PluginChats(installation, bob)
    chat = await mine.record("repo", "acme/app", "thread-1")
    read_page = ClientToolSpec(
        kind="function", name="docs__read_page", parameters={"type": "object", "properties": {}}
    )

    # Opening it again keeps the thread it was made with.
    assert (await mine.record("repo", "acme/app", "thread-2")).thread_id == "thread-1"
    assert await theirs.by_id(chat.id) is None
    assert not await theirs.offer(chat.id, [read_page], "")
    assert await mine.offer(chat.id, [read_page], "Read the page first.")
    assert await PluginChat.run_config("thread-1", bob) is None
    config = await PluginChat.run_config("thread-1", ada)
    assert config is not None
    assert (config.prefix, [t.name for t in config.tools]) == ("docs__", ["docs__read_page"])

    # A replayed tool call is one call; one dock claims it, and it is answered once.
    await chat.calls.record("call-1", "docs__read_page", {})
    await chat.calls.record("call-1", "docs__read_page", {})
    assert [call.call_id for call in await chat.calls.claim()] == ["call-1"]
    assert await chat.calls.claim() == []
    assert await chat.calls.answer({"call-1": "# Hi"}, by=ada) == ["call-1"]
    assert await chat.calls.answer({"call-1": "# Again"}, by=ada) == []
    # A resume that never started leaves the call to be answered again.
    await chat.calls.reopen(["call-1"])
    assert await chat.calls.answer({"call-1": "# Hi"}, by=ada) == ["call-1"]
    assert await chat.calls.pending() == []

    # The plugin's results resume the chat only so often before the person writes again,
    # and what no run delivered goes to the agent with their message, once.
    assert [await chat.calls.spend_resume(2) for _ in range(3)] == [True, True, False]
    assert [m["content"] for m in await PluginChat.person_wrote("thread-1", ada)] == ["# Hi"]
    assert await PluginChat.person_wrote("thread-1", bob) == []
    held = await PluginChat.person_wrote("thread-1", ada)
    assert [m["content"] for m in held] == ["# Hi"]
    await PluginChat.deliver_input("thread-1", bob, {"messages": held})
    assert await PluginChat.person_wrote("thread-1", ada) == held
    await PluginChat.deliver_input("thread-1", ada, {"messages": held})
    assert await PluginChat.person_wrote("thread-1", ada) == []
    assert await chat.calls.spend_resume(2)


async def test_existing_chats_lose_access_when_repository_or_agent_grants_are_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ada = await _person("ada", "1")
    installation_id = await _install(ada, _bundle(permissions=["agent"]))
    await PluginInstallations.share(installation_id, ada, ["acme/app"])
    chats = PluginChats(installation_id, ada)
    chat = await chats.record("repo", "acme/app", "thread-1")
    await chats.offer(
        chat.id,
        [ClientToolSpec(kind="function", name="docs__read_page", parameters={"type": "object"})],
        "Read the page first.",
    )
    readable = True

    async def can_read(_login: str, _repository: str) -> bool:
        return readable

    monkeypatch.setattr(installed_module, "can_read_repository", can_read)
    plugin = _installed(await PluginInstallations.get(installation_id, ada), ada)
    assert (await plugin.own_chat(chat.id)).id == chat.id
    assert await PluginChat.run_config(chat.thread_id, ada) is not None

    readable = False
    with pytest.raises(RepositoryNotGranted):
        await plugin.own_chat(chat.id)
    assert await PluginChat.run_config(chat.thread_id, ada) is None

    readable = True
    await PluginInstallations.unshare(installation_id, ada, "acme/app")
    plugin = _installed(await PluginInstallations.get(installation_id, ada), ada)
    with pytest.raises(RepositoryNotGranted):
        await plugin.own_chat(chat.id)
    assert await PluginChat.run_config(chat.thread_id, ada) is None

    await PluginInstallations.share(installation_id, ada, ["acme/app"])
    version = await PluginApps.publish(ada, _bundle(permissions=[]), origin={})
    await PluginInstallations.upgrade(installation_id, version, by=ada)
    plugin = _installed(await PluginInstallations.get(installation_id, ada), ada)
    with pytest.raises(PluginPermissionDenied):
        await plugin.own_chat(chat.id)
    assert await PluginChat.run_config(chat.thread_id, ada) is None


async def test_a_new_preview_forgets_the_build_it_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No grace: every bundle counts as old enough that no version is about to use it.
    monkeypatch.setattr(records_module, "BUNDLE_GRACE", timedelta(0))
    ada = await _person("ada", "1")
    for body in ("<p>first</p>", "<p>second</p>"):
        version = await PluginApps.publish(
            ada, _bundle(permissions=[], body=body), origin={}, preview_of="thread-1"
        )
        assert await PluginInstallations.preview("thread-1", version, user_id=ada)

    async with postgres.session() as session:
        stored = list(await session.scalars(text("SELECT html FROM plugin_bundle")))
    assert [html.endswith("<p>second</p>") for html in stored] == [True]
    running = await PluginInstallations.previewing("thread-1", ada)
    assert running is not None
    assert (await PluginInstallations.bundle(running.id)).endswith("<p>second</p>")
    # A preview runs beside its thread, not in the sidebar.
    assert await PluginInstallations.visible_to(ada) == []


async def test_a_preview_asking_for_more_waits_for_the_person() -> None:
    ada = await _person("ada", "1")
    first = await PluginApps.publish(ada, _bundle(permissions=[]), origin={}, preview_of="t")
    assert await PluginInstallations.preview("t", first, user_id=ada)
    wider = await PluginApps.publish(
        ada, _bundle(permissions=["github:write"]), origin={}, preview_of="t"
    )

    assert not await PluginInstallations.preview("t", wider, user_id=ada)
    running = await PluginInstallations.previewing("t", ada)
    assert running is not None and running.version.id == first.id
    assert await PluginApps.newest_preview("t", ada) == wider

    assert await PluginInstallations.preview(
        "t", wider, user_id=ada, approved=[PluginPermission.GITHUB_WRITE]
    )
    running = await PluginInstallations.previewing("t", ada)
    assert running is not None and running.version.id == wider.id


async def test_a_preview_cannot_be_replaced_by_another_person_or_keep_another_apps_grants() -> None:
    ada, bob = await _person("ada", "1"), await _person("bob", "2")
    first = await PluginApps.publish(ada, _bundle(permissions=[]), origin={}, preview_of="t")
    assert await PluginInstallations.preview("t", first, user_id=ada)
    running = await PluginInstallations.previewing("t", ada)
    assert running is not None
    await PluginInstallations.share(running.id, ada, ["acme/private"])
    impostor = await PluginApps.publish(bob, _bundle(permissions=[]), origin={}, preview_of="t")
    assert not await PluginInstallations.preview("t", impostor, user_id=bob)
    assert (await PluginInstallations.get(running.id, ada)).version.id == first.id
    replacement = await PluginApps.publish(
        ada, _bundle(name="other", permissions=[]), origin={}, preview_of="t"
    )
    assert await PluginInstallations.preview("t", replacement, user_id=ada)
    async with postgres.session() as session:
        assert not list(await session.scalars(text("SELECT id FROM plugin_repository_grant")))


async def test_only_listed_apps_reach_other_people_and_a_name_is_added_once() -> None:
    ada, bob = await _person("ada", "1"), await _person("bob", "2")
    version = await PluginApps.publish(ada, _bundle(), origin={})

    with pytest.raises(AppNotFound):
        await PluginApps.version(version.id, bob)
    with pytest.raises(AppNotFound):
        await PluginApps.set_listed(version.app, bob, True)
    await PluginApps.set_listed(version.app, ada, True)
    await PluginInstallations.install(await PluginApps.version(version.id, bob), user_id=bob)

    assert [app.name for app in await PluginApps.available_to(bob)] == ["docs"]
    with pytest.raises(PluginNameTaken):
        await _install(bob)


def _installed(installation: Installation, user_id: UUID) -> InstalledPlugin:
    return InstalledPlugin(installation=installation, login="ada", email=None, user_id=user_id)


async def test_plugin_reaches_only_repositories_the_person_shared_and_can_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ada, bob = await _person("ada", "1"), await _person("bob", "2")
    installation_id = await _install(ada, _bundle(permissions=["storage", "github:read"]))
    await PluginInstallations.share(installation_id, ada, ["acme/app", "acme/secret"])
    plugin = _installed(await PluginInstallations.get(installation_id, ada), ada)
    # Ada's install is hers alone.
    with pytest.raises(InstallationNotFound):
        await PluginInstallations.get(installation_id, bob)

    async def readable(_login: str, repository: str) -> bool:
        return repository != "acme/secret"

    monkeypatch.setattr(installed_module, "can_read_repository", readable)

    assert await plugin.repository("Acme/App") == "acme/app"
    with pytest.raises(RepositoryNotGranted):
        await plugin.repository("acme/web")
    with pytest.raises(RepositoryNotGranted):
        await plugin.repository("acme/secret")
    with pytest.raises(PluginPermissionDenied):
        plugin.require(PluginPermission.GITHUB_WRITE)
    with pytest.raises(PluginPermissionDenied):
        await plugin.room("draft", "acme/app")


async def test_compacting_a_room_keeps_what_came_after() -> None:
    ada = await _person("ada", "1")
    plugin = (await PluginInstallations.get(await _install(ada), ada)).plugin
    room = Room(plugin, DocumentScope(repository="acme/app"), "draft/page.md")
    other = Room(plugin, DocumentScope(repository="acme/web"), "draft/page.md")
    first = await room.append(b"one", by=ada)
    await room.append(b"two", by=ada)
    await other.append(b"elsewhere", by=ada)
    second = await room.compact(b"one+two", through=first + 1, by=ada)
    await room.append(b"three", by=ada)

    assert [u.payload for u in await room.since(0)] == [b"one+two", b"three"]
    assert [u.payload for u in await room.since(second)] == [b"three"]
    assert [u.payload for u in await other.since(0)] == [b"elsewhere"]


async def test_room_updates_commit_in_sequence_order(monkeypatch: pytest.MonkeyPatch) -> None:
    ada = await _person("ada", "1")
    plugin = (await PluginInstallations.get(await _install(ada), ada)).plugin
    room = Room(plugin, DocumentScope(repository="acme/app"), "draft/page.md")
    inserted, release = asyncio.Event(), asyncio.Event()
    notify = Room._notify

    async def hold_first(self, session, event):
        if not inserted.is_set():
            inserted.set()
            await release.wait()
        await notify(self, session, event)

    monkeypatch.setattr(Room, "_notify", hold_first)
    first = asyncio.create_task(room.append(b"one", by=ada))
    second = None
    try:
        await asyncio.wait_for(inserted.wait(), 2)
        second = asyncio.create_task(room.append(b"two", by=ada))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), 0.1)
        assert await room.since(0) == []
    finally:
        release.set()
        await first
        if second is not None:
            await second
    assert [update.payload for update in await room.since(0)] == [b"one", b"two"]


async def test_document_changes_reach_only_people_who_can_read_their_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ada, bob = await _person("ada", "1"), await _person("bob", "2")
    plugin = (await PluginInstallations.get(await _install(ada), ada)).plugin

    async def readable(_login: str, repository: str) -> bool:
        return repository == "acme/app"

    monkeypatch.setattr(installed_module, "can_read_repository", readable)
    keys = {
        DocumentScope(repository="acme/app").topic_key(plugin, "comments"),
        DocumentScope(repository="acme/secret").topic_key(plugin, "comments"),
        DocumentScope(user_id=ada).topic_key(plugin, "notes"),
        DocumentScope(user_id=bob).topic_key(plugin, "notes"),
    }

    allowed = await _readable_plugin_documents({"sub": "ada", "user_id": str(ada)}, keys)

    assert allowed == {
        DocumentScope(repository="acme/app").topic_key(plugin, "comments"),
        DocumentScope(user_id=ada).topic_key(plugin, "notes"),
    }
