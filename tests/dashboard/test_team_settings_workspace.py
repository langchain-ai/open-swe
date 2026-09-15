from agent.dashboard import team_settings
from agent.dashboard.team_settings import (
    TeamSettingsUpdate,
    get_team_settings,
    upsert_team_settings,
)
from agent.run_config import RunConfig
from tests.conftest import FakeStore


async def test_settings_are_isolated_per_workspace(fake_store: FakeStore) -> None:
    await upsert_team_settings(
        TeamSettingsUpdate(org_guidelines="internal only"), workspace="default"
    )
    await upsert_team_settings(TeamSettingsUpdate(org_guidelines="be public"), workspace="oss")
    assert (await get_team_settings("default"))["org_guidelines"] == "internal only"
    assert (await get_team_settings("oss"))["org_guidelines"] == "be public"
    assert (await get_team_settings("brand-new"))["org_guidelines"] is None


async def test_settings_workspace_comes_from_the_running_config(
    fake_store: FakeStore, monkeypatch
) -> None:
    await upsert_team_settings(TeamSettingsUpdate(org_guidelines="be public"), workspace="oss")
    monkeypatch.setattr(
        RunConfig, "from_runtime", classmethod(lambda cls: RunConfig(workspace="oss"))
    )
    assert (await get_team_settings())["org_guidelines"] == "be public"


def test_settings_workspace_defaults_outside_a_run(monkeypatch) -> None:
    def _raise(cls):
        raise RuntimeError("no runtime")

    monkeypatch.setattr(RunConfig, "from_runtime", classmethod(_raise))
    assert team_settings.resolve_settings_workspace() == "default"
    assert team_settings.resolve_settings_workspace("oss") == "oss"
