import asyncio

import pytest

from openswe import server
from openswe.dashboard.settings import Settings
from openswe.run_config import RunConfig
from openswe.users import User
from tests.conftest import FakeUserRecords


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            RunConfig.parse({"repo": {"owner": "octo", "name": "repo"}}),
            {"owner": "octo", "name": "repo"},
        ),
        (RunConfig(repo_explicitly_none=True), None),
        (RunConfig(github_linked=False), None),
    ],
)
def test_resolve_prompt_default_repo_never_loads_team_default(
    monkeypatch: pytest.MonkeyPatch, config: RunConfig, expected: dict[str, str] | None
) -> None:
    async def fake_get_settings() -> Settings:
        raise AssertionError("global settings should not be loaded")

    monkeypatch.setattr(server, "get_settings", fake_get_settings)

    assert asyncio.run(server._resolve_prompt_default_repo(config)) == expected


async def test_prompt_default_uses_the_users_global_repository(
    user_records: FakeUserRecords, registry_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    await User.sign_in("github", "alice", login="alice")
    user_records.seed(
        "profile",
        "alice",
        {"default_repo": "user/global", "workspace_default_repos": {"oss": "user/scoped"}},
    )
    repo = await server._resolve_prompt_default_repo(RunConfig(github_login="alice"))
    assert repo == {"owner": "user", "name": "global"}


def test_resolve_prompt_default_repo_falls_back_to_team_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []

    async def fake_get_settings() -> Settings:
        seen.append(True)
        return Settings({"default_repo": "team/repo"})

    monkeypatch.setattr(server, "get_settings", fake_get_settings)

    repo = asyncio.run(server._resolve_prompt_default_repo(RunConfig()))

    assert repo == {"owner": "team", "name": "repo"}
    assert seen == [True]
