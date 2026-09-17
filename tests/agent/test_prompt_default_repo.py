import asyncio

import pytest

from agent import server
from agent.dashboard.workspace_settings import WorkspaceSettings
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
    async def fake_get_workspace_settings(workspace: str | None = None) -> WorkspaceSettings:
        raise AssertionError("workspace settings should not be loaded")

    monkeypatch.setattr(server, "get_workspace_settings", fake_get_workspace_settings)

    assert asyncio.run(server._resolve_prompt_default_repo(config)) == expected


def test_resolve_prompt_default_repo_falls_back_to_team_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []

    async def fake_get_workspace_settings(workspace: str | None = None) -> WorkspaceSettings:
        seen.append(workspace)
        return WorkspaceSettings({"default_repo": "team/repo"})

    monkeypatch.setattr(server, "get_workspace_settings", fake_get_workspace_settings)

    repo = asyncio.run(server._resolve_prompt_default_repo(RunConfig(workspace="oss")))

    assert repo == {"owner": "team", "name": "repo"}
    assert seen == ["oss"]
