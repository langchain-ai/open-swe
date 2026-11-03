import pytest

from openswe.projects import store
from openswe.projects.store import (
    PROJECTS,
    ProjectChanges,
    ProjectConflict,
    ProjectForbidden,
    ProjectNotFound,
)
from openswe.users import User


@pytest.fixture(autouse=True)
def people(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada,bob,eve")

    async def granted(login: str, full_name: str) -> str:
        return "token"

    monkeypatch.setattr(store, "require_repo_access_for_user", granted)


async def test_only_owners_change_a_shared_project_and_it_keeps_one(registry_db: None) -> None:
    ada = await User.sign_in("github", "1", login="ada")
    bob = await User.sign_in("github", "2", login="bob")
    eve = await User.sign_in("github", "3", login="eve")
    project = await PROJECTS.create(ada, "Website")

    with pytest.raises(ProjectNotFound):
        await PROJECTS.get(project.id, bob)
    await PROJECTS.set_member(project.id, ada, bob, "member")
    assert [p.id for p in await PROJECTS.for_member(bob)] == [project.id]

    with pytest.raises(ProjectForbidden):
        await PROJECTS.update(project.id, bob, ProjectChanges(name="Mine now"))
    with pytest.raises(ProjectForbidden):
        await PROJECTS.set_member(project.id, bob, eve, "owner")
    with pytest.raises(ProjectForbidden):
        await PROJECTS.delete(project.id, bob)
    with pytest.raises(ProjectNotFound):
        await PROJECTS.set_member(project.id, eve, eve, "owner")

    with pytest.raises(ProjectConflict):
        await PROJECTS.remove_member(project.id, ada, ada)
    with pytest.raises(ProjectConflict):
        await PROJECTS.set_member(project.id, ada, ada, "member")
    await PROJECTS.set_member(project.id, ada, bob, "owner")
    await PROJECTS.remove_member(project.id, ada, ada)
    assert await PROJECTS.for_member(ada) == []
    assert (await PROJECTS.update(project.id, bob, ProjectChanges(name="Site"))).name == "Site"


async def test_a_repository_files_under_one_project_for_its_members(registry_db: None) -> None:
    ada = await User.sign_in("github", "1", login="ada")
    bob = await User.sign_in("github", "2", login="bob")
    project = await PROJECTS.create(ada, "Docs", repo_full_name="acme/docs")
    with pytest.raises(ProjectConflict):
        await PROJECTS.create(bob, "Also docs", repo_full_name="Acme/Docs")

    in_repo = {"repo_owner": "acme", "repo_name": "docs"}
    started_there = {"project_id": str(project.id)}
    assert (await PROJECTS.filing_for("ada")).project_for(in_repo, "acme/docs") == str(project.id)
    assert (await PROJECTS.filing_for("ada")).project_for(started_there, "") == str(project.id)
    assert (await PROJECTS.filing_for("bob")).project_for(in_repo, "acme/docs") is None
    assert (await PROJECTS.filing_for("bob")).project_for(started_there, "") is None
