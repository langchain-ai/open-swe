from agent.dashboard import team_settings, team_settings_cache
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


async def test_cached_reads_do_not_leak_across_workspaces(fake_store: FakeStore) -> None:
    """The TTL cache is process-global, so its keys must carry the workspace."""
    await upsert_team_settings(
        TeamSettingsUpdate(org_guidelines="internal only", fable_enabled=True), workspace="default"
    )
    await upsert_team_settings(
        TeamSettingsUpdate(org_guidelines="be public", fable_enabled=False), workspace="oss"
    )

    assert await team_settings_cache.cached_org_review_guidelines("default") == "internal only"
    assert await team_settings_cache.cached_org_review_guidelines("oss") == "be public"
    assert await team_settings_cache.cached_fable_enabled("default") is True
    assert await team_settings_cache.cached_fable_enabled("oss") is False
    assert (await team_settings_cache.cached_team_settings("oss"))["org_guidelines"] == "be public"
    assert (await team_settings_cache.cached_team_settings("default"))[
        "org_guidelines"
    ] == "internal only"
