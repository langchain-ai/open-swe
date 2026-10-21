"""PostgreSQL regressions for what keeps plugins inside the access they were given."""

from uuid import UUID

import pytest

from openswe.github.repo_names import RepoFullName
from openswe.plugins import installed as installed_module
from openswe.plugins.installed import InstalledPlugin, PluginPermissionDenied, RepositoryNotGranted
from openswe.plugins.manifest import PluginManifest, PluginPermission
from openswe.plugins.records import (
    DocumentScope,
    Installation,
    PluginDocuments,
    PluginInstallations,
    PluginNameTaken,
)
from openswe.plugins.rooms import Room
from openswe.plugins.source import PluginPackage, PluginSource
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
    first = await PluginInstallations.install(
        _package("acme/docs"), user_id=ada, repositories=["acme/app"], by=ada
    )
    # The same name from another repository is another plugin with its own data.
    impostor = await PluginInstallations.install(
        _package("evil/docs"), user_id=bob, repositories=["acme/app"], by=bob
    )
    docs = PluginDocuments((await PluginInstallations.get(first, ada)).plugin)
    other = PluginDocuments((await PluginInstallations.get(impostor, bob)).plugin)
    shared = DocumentScope(repository="acme/app")

    await docs.put("comments", shared, "c1", {"body": "shared"}, by=ada)
    await docs.put("notes", DocumentScope(user_id=ada), "n1", {"body": "ada only"}, by=ada)

    assert await other.get("comments", shared, "c1") is None
    assert await docs.get("comments", DocumentScope(repository="acme/web"), "c1") is None
    assert await docs.get("notes", DocumentScope(user_id=bob), "n1") is None
    assert [d.key for d in await docs.query("comments", shared, {"body": "shared"}, limit=10)] == [
        "c1"
    ]


async def test_a_name_installed_for_everyone_wins_and_cannot_be_installed_twice() -> None:
    ada = await _person("ada", "1")
    own = await PluginInstallations.install(
        _package("acme/docs"), user_id=ada, repositories=[], by=ada
    )
    with pytest.raises(PluginNameTaken):
        await PluginInstallations.install(
            _package("other/docs"), user_id=ada, repositories=[], by=ada
        )
    everyone = await PluginInstallations.install(
        _package("acme/docs"), user_id=None, repositories=[], by=ada
    )

    visible = await PluginInstallations.visible_to(ada)

    assert [i.id for i in visible] == [everyone]
    assert own != everyone


def _installed(installation: Installation, user_id: UUID) -> InstalledPlugin:
    return InstalledPlugin(installation=installation, login="ada", email=None, user_id=user_id)


async def test_plugin_reaches_only_granted_repositories_the_person_can_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ada = await _person("ada", "1")
    installation_id = await PluginInstallations.install(
        _package("acme/docs", permissions=["storage", "github:read"]),
        user_id=ada,
        repositories=["acme/app", "acme/secret"],
        by=ada,
    )
    plugin = _installed(await PluginInstallations.get(installation_id, ada), ada)

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
    installation_id = await PluginInstallations.install(
        _package("acme/docs"), user_id=ada, repositories=[], by=ada
    )
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
