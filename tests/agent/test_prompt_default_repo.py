import pytest

from agent import server
from agent.github.repositories import Repository
from agent.run_config import RunConfig
from tests.support.repositories import FakeRepositories


@pytest.mark.asyncio
async def test_resolve_prompt_repositories_reads_the_legacy_single_repo(
    monkeypatch: pytest.MonkeyPatch, fake_repositories: FakeRepositories
) -> None:
    async def fake_get_team_default_repo(workspace: str | None = None) -> dict[str, str] | None:
        raise AssertionError("team default should not be loaded")

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    config = RunConfig.parse({"repo": {"owner": "octo", "name": "repo"}})

    assert [r.full_name for r in await server._resolve_prompt_repositories(config)] == ["octo/repo"]


@pytest.mark.asyncio
async def test_resolve_prompt_repositories_reads_every_linked_repository(
    monkeypatch: pytest.MonkeyPatch, fake_repositories: FakeRepositories
) -> None:
    async def fake_get_team_default_repo(workspace: str | None = None) -> dict[str, str] | None:
        raise AssertionError("team default should not be loaded")

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    one = fake_repositories.add("octo/one")
    two = fake_repositories.add("octo/two")
    config = RunConfig.parse({"repository_ids": [str(one.id), str(two.id)]})

    assert await server._resolve_prompt_repositories(config) == [one, two]


@pytest.mark.asyncio
async def test_resolve_prompt_repositories_skips_the_team_default_when_none_is_wanted(
    monkeypatch: pytest.MonkeyPatch, fake_repositories: FakeRepositories
) -> None:
    async def fake_get_team_default_repo(workspace: str | None = None) -> dict[str, str] | None:
        raise AssertionError("team default should not be loaded")

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    assert await server._resolve_prompt_repositories(RunConfig(repo_explicitly_none=True)) == []


@pytest.mark.asyncio
async def test_resolve_prompt_repositories_falls_back_to_team_default(
    monkeypatch: pytest.MonkeyPatch, fake_repositories: FakeRepositories
) -> None:
    seen: list[str | None] = []

    async def fake_get_team_default_repo(workspace: str | None = None) -> dict[str, str] | None:
        seen.append(workspace)
        return {"owner": "team", "name": "repo"}

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    repositories = await server._resolve_prompt_repositories(RunConfig(workspace="oss"))

    assert [repository.full_name for repository in repositories] == ["team/repo"]
    assert seen == ["oss"]
    assert await Repository.get("team/repo") is not None
