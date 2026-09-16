"""Workspace settings resolve by tier: hardcoded defaults, the instance record, then a workspace's overrides."""

import pytest
from fastapi import HTTPException

from agent.dashboard import workspace_settings, workspace_settings_cache
from agent.dashboard.options import FABLE_MODEL_IDS
from agent.dashboard.options_routes import options
from agent.dashboard.workspace_settings import (
    INSTANCE_SETTINGS_NAMESPACE,
    WorkspaceSettingsUpdate,
    get_instance_settings,
    get_workspace_settings,
    upsert_instance_settings,
    upsert_workspace_overrides,
)
from agent.run_config import RunConfig
from agent.store import get_value
from agent.workspaces.store import WORKSPACES, WorkspaceCreate
from tests.conftest import FakeStore


async def test_workspaces_inherit_the_instance_record(fake_store: FakeStore) -> None:
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(org_guidelines="internal only", fable_enabled=True)
    )
    for slug in ("default", "oss", "never-configured"):
        settings = await get_workspace_settings(slug)
        assert settings["org_guidelines"] == "internal only"
        assert settings["fable_enabled"] is True


async def test_the_pre_workspaces_record_is_the_instance_record(fake_store: FakeStore) -> None:
    """An upgraded deployment keeps its settings without a migration."""
    fake_store.seed(INSTANCE_SETTINGS_NAMESPACE, "default", {"org_guidelines": "legacy rules"})
    assert (await get_instance_settings())["org_guidelines"] == "legacy rules"
    assert (await get_workspace_settings("oss"))["org_guidelines"] == "legacy rules"


async def test_records_written_beside_the_instance_one_still_apply(fake_store: FakeStore) -> None:
    """#2807 kept every workspace's record in the instance namespace; those survive the upgrade."""
    await upsert_instance_settings(WorkspaceSettingsUpdate(org_guidelines="internal only"))
    fake_store.seed(
        INSTANCE_SETTINGS_NAMESPACE, "oss", {"org_guidelines": "be public", "fable_enabled": None}
    )
    assert (await get_workspace_settings("oss"))["org_guidelines"] == "be public"

    # Saving the workspace moves it to its own record, so the old one cannot resurface.
    await upsert_workspace_overrides("oss", WorkspaceSettingsUpdate())
    assert (await get_workspace_settings("oss"))["org_guidelines"] == "internal only"
    assert await get_value(INSTANCE_SETTINGS_NAMESPACE, "oss") is None


async def test_an_override_applies_to_its_workspace_only(fake_store: FakeStore) -> None:
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(org_guidelines="internal only", fable_enabled=True)
    )
    view = await upsert_workspace_overrides(
        "oss", WorkspaceSettingsUpdate(org_guidelines="be public", fable_enabled=False)
    )
    assert view["effective"]["org_guidelines"] == "be public"
    assert view["effective"]["fable_enabled"] is False
    assert view["overrides"] == {"org_guidelines": "be public", "fable_enabled": False}
    assert (await get_workspace_settings("core"))["org_guidelines"] == "internal only"
    assert (await get_instance_settings())["org_guidelines"] == "internal only"


async def test_clearing_an_override_restores_inheritance(fake_store: FakeStore) -> None:
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(
            default_agent_model="anthropic:claude-sonnet-5",
            default_agent_reasoning_effort="high",
        )
    )
    await upsert_workspace_overrides(
        "oss",
        WorkspaceSettingsUpdate(
            default_agent_model="openai:gpt-6-astra", default_agent_reasoning_effort="low"
        ),
    )
    assert (await get_workspace_settings("oss"))["default_agent_model"] == "openai:gpt-6-astra"

    view = await upsert_workspace_overrides("oss", WorkspaceSettingsUpdate())
    assert view["overrides"] == {}
    assert view["effective"]["default_agent_model"] == "anthropic:claude-sonnet-5"
    assert view["effective"]["default_agent_reasoning_effort"] == "high"


async def test_fable_rules_follow_the_toggle_a_workspace_resolves_to(fake_store: FakeStore) -> None:
    fable_model = "anthropic:claude-fable-5-1"

    # Fable on at the instance: a workspace inheriting the toggle cannot save it as a default.
    await upsert_instance_settings(WorkspaceSettingsUpdate(fable_enabled=True))
    with pytest.raises(ValueError, match="cannot be a default model"):
        await upsert_workspace_overrides(
            "oss",
            WorkspaceSettingsUpdate(
                default_agent_model=fable_model, default_agent_reasoning_effort="high"
            ),
        )

    # Fable off at the instance: the kill switch swaps the selection for a safe fallback.
    await upsert_instance_settings(WorkspaceSettingsUpdate(fable_enabled=False))
    view = await upsert_workspace_overrides(
        "oss",
        WorkspaceSettingsUpdate(
            default_agent_model=fable_model, default_agent_reasoning_effort="high"
        ),
    )
    assert view["overrides"]["default_agent_model"] not in FABLE_MODEL_IDS


async def test_settings_workspace_comes_from_the_running_config(
    fake_store: FakeStore, monkeypatch
) -> None:
    await upsert_workspace_overrides("oss", WorkspaceSettingsUpdate(org_guidelines="be public"))
    monkeypatch.setattr(
        RunConfig, "from_runtime", classmethod(lambda cls: RunConfig(workspace="oss"))
    )
    assert (await get_workspace_settings())["org_guidelines"] == "be public"


def test_settings_workspace_defaults_outside_a_run(monkeypatch) -> None:
    def _raise(cls):
        raise RuntimeError("no runtime")

    monkeypatch.setattr(RunConfig, "from_runtime", classmethod(_raise))
    assert workspace_settings.resolve_settings_workspace() == "default"
    assert workspace_settings.resolve_settings_workspace("oss") == "oss"


async def test_cached_reads_do_not_leak_across_workspaces(fake_store: FakeStore) -> None:
    """The TTL cache is process-global, so its keys must carry the workspace."""
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(org_guidelines="internal only", fable_enabled=True)
    )
    await upsert_workspace_overrides(
        "oss", WorkspaceSettingsUpdate(org_guidelines="be public", fable_enabled=False)
    )

    assert await workspace_settings_cache.cached_org_review_guidelines("default") == "internal only"
    assert await workspace_settings_cache.cached_org_review_guidelines("oss") == "be public"
    assert await workspace_settings_cache.cached_fable_enabled("default") is True
    assert await workspace_settings_cache.cached_fable_enabled("oss") is False
    assert (await workspace_settings_cache.cached_workspace_settings("oss"))[
        "org_guidelines"
    ] == "be public"
    assert (await workspace_settings_cache.cached_workspace_settings("default"))[
        "org_guidelines"
    ] == "internal only"


async def test_options_report_the_requested_workspaces_effective_defaults(
    fake_store: FakeStore,
) -> None:
    """The composer's model picker is scoped to the workspace it composes in."""
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(
            default_agent_model="anthropic:claude-sonnet-5",
            default_agent_reasoning_effort="high",
        )
    )
    await upsert_workspace_overrides(
        "oss",
        WorkspaceSettingsUpdate(
            default_agent_model="openai:gpt-6-astra",
            default_agent_reasoning_effort="low",
            fable_enabled=True,
        ),
    )

    scoped = await options(workspace="oss")
    unscoped = await options()

    assert scoped["default_agent_model"] == "openai:gpt-6-astra"
    assert scoped["default_agent_reasoning_effort"] == "low"
    assert unscoped["default_agent_model"] == "anthropic:claude-sonnet-5"
    # The Fable flag follows the same tiers, so the selectable list follows it.
    assert [m["id"] for m in scoped["models"] if m["id"] in FABLE_MODEL_IDS]
    assert not [m["id"] for m in unscoped["models"] if m["id"] in FABLE_MODEL_IDS]


async def test_options_refuse_a_workspace_name_that_does_not_slugify() -> None:
    with pytest.raises(HTTPException) as refused:
        await options(workspace="!!!")
    assert refused.value.status_code == 400


async def test_workspace_settings_api_refuses_unknown_and_unslugifiable_names(
    fake_store: FakeStore, registry_db
) -> None:
    """A name the store could never hold is a bad request; a name it does not hold is 404."""
    with pytest.raises(HTTPException) as refused:
        await workspace_settings.api_get_workspace_settings(
            workspace="!!!", _session={"sub": "alice"}
        )
    assert refused.value.status_code == 400
    with pytest.raises(HTTPException) as missing:
        await workspace_settings.api_get_workspace_settings(
            workspace="oss", _session={"sub": "alice"}
        )
    assert missing.value.status_code == 404


async def test_workspace_settings_api_round_trips_overrides(
    fake_store: FakeStore, registry_db
) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "alice")
    await upsert_instance_settings(WorkspaceSettingsUpdate(review_draft_prs=True))

    saved = await workspace_settings.api_put_workspace_settings(
        workspace=" OSS ",
        body=WorkspaceSettingsUpdate(review_draft_prs=False),
        _admin={"sub": "alice"},
    )
    assert saved["overrides"] == {"review_draft_prs": False}
    assert saved["effective"]["review_draft_prs"] is False

    fetched = await workspace_settings.api_get_workspace_settings(
        workspace="oss", _session={"sub": "alice"}
    )
    assert fetched == saved
    assert (await workspace_settings.api_get_instance_settings(_session={"sub": "alice"}))[
        "review_draft_prs"
    ] is True
