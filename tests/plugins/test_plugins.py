"""PostgreSQL regressions for what keeps plugins inside the access they were given."""

from typing import cast
from uuid import UUID

import pytest

from openswe.github.http import GitHubClient, Release, ReleaseAsset
from openswe.github.repo_names import RepoFullName
from openswe.plugins import installed as installed_module
from openswe.plugins.installed import InstalledPlugin, PluginPermissionDenied, RepositoryNotGranted
from openswe.plugins.manifest import PluginManifest, PluginPermission
from openswe.plugins.records import (
    Cursor,
    DocumentQuery,
    DocumentScope,
    Installation,
    PluginDocuments,
    PluginInstallations,
    PluginNameTaken,
)
from openswe.plugins.rooms import Room
from openswe.plugins.source import PluginPackage, PluginSource, PluginSourceError
from openswe.ui_invalidations.topics import _readable_plugin_documents
from openswe.users import User

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada,bob")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


def _package(
    repository: str, name: str = "docs", permissions: list[str] | None = None
) -> PluginPackage:
    manifest = PluginManifest.model_validate(
        {
            "name": name,
            "displayName": "Docs",
            "version": "1.0.0",
            "entry": "bundle/index.html",
            "sidebar": {"label": "Docs"},
            "permissions": permissions if permissions is not None else ["storage"],
        }
    )
    return PluginPackage(
        source=PluginSource(RepoFullName(repository)),
        ref="main",
        commit_sha="a" * 40,
        manifest=manifest,
        html="<p>docs</p>",
    )


async def _person(login: str, external_id: str) -> UUID:
    return (await User.sign_in("github", external_id, login=login)).id


async def test_documents_stay_in_their_plugin_and_scope() -> None:
    ada, bob = await _person("ada", "1"), await _person("bob", "2")
    first = await PluginInstallations.install(_package("acme/docs"), user_id=ada, by=ada)
    # The same name from another repository is another plugin with its own data.
    impostor = await PluginInstallations.install(_package("evil/docs"), user_id=bob, by=bob)
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
    installation = await PluginInstallations.install(_package("acme/docs"), user_id=ada, by=ada)
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


async def test_a_name_installed_for_everyone_wins_and_cannot_be_installed_twice() -> None:
    ada = await _person("ada", "1")
    own = await PluginInstallations.install(_package("acme/docs"), user_id=ada, by=ada)
    with pytest.raises(PluginNameTaken):
        await PluginInstallations.install(_package("other/docs"), user_id=ada, by=ada)
    everyone = await PluginInstallations.install(_package("acme/docs"), user_id=None, by=ada)

    visible = await PluginInstallations.visible_to(ada)

    assert [i.id for i in visible] == [everyone]
    assert own != everyone


def _installed(installation: Installation, user_id: UUID) -> InstalledPlugin:
    return InstalledPlugin(installation=installation, login="ada", email=None, user_id=user_id)


async def test_plugin_reaches_only_repositories_the_person_shared_and_can_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ada, bob = await _person("ada", "1"), await _person("bob", "2")
    installation_id = await PluginInstallations.install(
        _package("acme/docs", permissions=["storage", "github:read"]), user_id=None, by=ada
    )
    await PluginInstallations.share(installation_id, ada, ["acme/app", "acme/secret"])
    plugin = _installed(await PluginInstallations.get(installation_id, ada), ada)
    # Ada's picks are hers: the same install reaches nothing for Bob until he picks.
    for_bob = _installed(await PluginInstallations.get(installation_id, bob), bob)

    async def readable(_login: str, repository: str) -> bool:
        return repository != "acme/secret"

    monkeypatch.setattr(installed_module, "can_read_repository", readable)

    assert await plugin.repository("Acme/App") == "acme/app"
    with pytest.raises(RepositoryNotGranted):
        await for_bob.repository("acme/app")
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
    installation_id = await PluginInstallations.install(_package("acme/docs"), user_id=ada, by=ada)
    plugin = (await PluginInstallations.get(installation_id, ada)).plugin
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


async def test_document_changes_reach_only_people_who_can_read_their_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ada, bob = await _person("ada", "1"), await _person("bob", "2")
    plugin = (
        await PluginInstallations.get(
            await PluginInstallations.install(_package("acme/docs"), user_id=ada, by=ada), ada
        )
    ).plugin

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


class _PluginRepo:
    """A plugin repository whose head moved past its newest build."""

    head = "b" * 40

    def __init__(self, release: Release | None) -> None:
        self._release = release
        self.tags: list[str] = []

    async def info(self) -> dict[str, str]:
        return {"default_branch": "main"}

    async def commit_sha(self, ref: str) -> str:
        return self.head

    async def read_file(self, path: str, ref: str, *, max_chars: int, strict: bool) -> str:
        return _package("acme/docs").manifest.model_dump_json(by_alias=True)

    async def release(self, tag: str) -> Release | None:
        self.tags.append(tag)
        return self._release

    async def download_asset(self, asset: ReleaseAsset) -> bytes:
        return f"<p>{asset.name}</p>".encode()


async def test_an_install_takes_the_newest_build_and_pins_its_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    older, newer = "1" * 40, "2" * 40
    release = Release(
        tag_name="openswe-plugin/docs/main",
        assets=[
            ReleaseAsset(
                id=1, name=f"docs-{older}.html", size=9, updated_at="2026-10-20T00:00:00Z"
            ),
            ReleaseAsset(
                id=2, name=f"docs-{newer}.html", size=9, updated_at="2026-10-21T00:00:00Z"
            ),
            ReleaseAsset(id=3, name="other-plugin.html", size=9, updated_at="2026-10-22T00:00:00Z"),
        ],
    )
    repo = _PluginRepo(release)
    monkeypatch.setattr(PluginSource, "repo", lambda _self, _github: repo)

    package = await PluginSource(RepoFullName("acme/docs")).fetch(cast(GitHubClient, None))

    assert repo.tags == ["openswe-plugin/docs/main"]
    assert (package.commit_sha, package.html) == (newer, f"<p>docs-{newer}.html</p>")

    monkeypatch.setattr(PluginSource, "repo", lambda _self, _github: _PluginRepo(None))
    with pytest.raises(PluginSourceError, match="no build of docs on main"):
        await PluginSource(RepoFullName("acme/docs")).fetch(cast(GitHubClient, None))
