import asyncio

import pytest

from agent import server
from agent.run_config import RunConfig


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            RunConfig.parse({"repo": {"owner": "octo", "name": "repo"}}),
            {"owner": "octo", "name": "repo"},
        ),
        (RunConfig(repo_explicitly_none=True), None),
    ],
)
def test_resolve_prompt_default_repo_never_loads_team_default(
    monkeypatch: pytest.MonkeyPatch, config: RunConfig, expected: dict[str, str] | None
) -> None:
    async def fake_get_team_default_repo() -> dict[str, str] | None:
        raise AssertionError("team default should not be loaded")

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    assert asyncio.run(server._resolve_prompt_default_repo(config)) == expected


def test_resolve_prompt_default_repo_falls_back_to_team_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_team_default_repo() -> dict[str, str] | None:
        return {"owner": "team", "name": "repo"}

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    repo = asyncio.run(server._resolve_prompt_default_repo(RunConfig()))

    assert repo == {"owner": "team", "name": "repo"}
