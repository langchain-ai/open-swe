import asyncio

import pytest

from agent import server
from agent.run_config import Repo, RunConfig


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            RunConfig.parse({"repo": {"owner": "octo", "name": "repo"}}),
            [Repo(owner="octo", name="repo")],
        ),
        (
            RunConfig.parse(
                {"repos": [{"owner": "octo", "name": "one"}, {"owner": "octo", "name": "two"}]}
            ),
            [Repo(owner="octo", name="one"), Repo(owner="octo", name="two")],
        ),
        (RunConfig(repo_explicitly_none=True), []),
    ],
)
def test_resolve_prompt_repositories_never_loads_team_default(
    monkeypatch: pytest.MonkeyPatch, config: RunConfig, expected: list[Repo]
) -> None:
    async def fake_get_team_default_repo() -> dict[str, str] | None:
        raise AssertionError("team default should not be loaded")

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    assert asyncio.run(server._resolve_prompt_repositories(config)) == expected


def test_resolve_prompt_repositories_falls_back_to_team_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []

    async def fake_get_team_default_repo(workspace: str | None = None) -> dict[str, str] | None:
        seen.append(workspace)
        return {"owner": "team", "name": "repo"}

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    repos = asyncio.run(server._resolve_prompt_repositories(RunConfig(workspace="oss")))

    assert repos == [Repo(owner="team", name="repo")]
    assert seen == ["oss"]
