import pytest
from fastapi import HTTPException

from agent.dashboard import team_settings, team_settings_cache
from agent.dashboard.options import FABLE_MODEL_IDS
from agent.dashboard.options_routes import options
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


async def test_options_report_the_requested_workspaces_defaults(fake_store: FakeStore) -> None:
    """The composer's model picker is scoped to the workspace it composes in."""
    await upsert_team_settings(
        TeamSettingsUpdate(
            default_agent_model="anthropic:claude-sonnet-5",
            default_agent_reasoning_effort="high",
        ),
        workspace="default",
    )
    await upsert_team_settings(
        TeamSettingsUpdate(
            default_agent_model="openai:gpt-6-astra",
            default_agent_reasoning_effort="low",
            fable_enabled=True,
        ),
        workspace="oss",
    )

    scoped = await options(workspace="oss")
    unscoped = await options()

    assert scoped["default_agent_model"] == "openai:gpt-6-astra"
    assert scoped["default_agent_reasoning_effort"] == "low"
    assert unscoped["default_agent_model"] == "anthropic:claude-sonnet-5"
    # The Fable flag is per workspace as well, so the selectable list follows it.
    assert [m["id"] for m in scoped["models"] if m["id"] in FABLE_MODEL_IDS]
    assert not [m["id"] for m in unscoped["models"] if m["id"] in FABLE_MODEL_IDS]


async def test_options_refuse_a_workspace_name_that_does_not_slugify() -> None:
    with pytest.raises(HTTPException) as refused:
        await options(workspace="!!!")
    assert refused.value.status_code == 400


async def test_a_workspace_name_that_does_not_slugify_is_refused(fake_store: FakeStore) -> None:
    """A name the store could never hold is a bad request, not a read of `default`."""
    with pytest.raises(HTTPException) as refused:
        await team_settings.api_get_team_settings(workspace="!!!", _session={"sub": "alice"})
    assert refused.value.status_code == 400

    assert (
        await team_settings.api_get_team_settings(workspace=" OSS ", _session={"sub": "alice"})
    )["org_guidelines"] is None
