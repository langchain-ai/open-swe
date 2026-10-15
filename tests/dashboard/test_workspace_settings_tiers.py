"""Workspace settings resolve by tier: hardcoded defaults, the instance record, then a workspace's overrides."""

import pytest
from fastapi import HTTPException, Request

from openswe.audit_logs.models import AuditLog
from openswe.dashboard import workspace_settings, workspace_settings_cache
from openswe.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    get_instance_settings,
    get_workspace_settings,
    upsert_instance_settings,
    upsert_workspace_overrides,
)
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate


async def test_an_override_applies_to_its_workspace_only(registry_db) -> None:
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


async def test_clearing_an_override_restores_inheritance(registry_db) -> None:
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(
            default_agent_model="anthropic:claude-opus-5-5",
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
    assert view["effective"]["default_agent_model"] == "anthropic:claude-opus-5-5"
    assert view["effective"]["default_agent_reasoning_effort"] == "high"


async def test_cached_reads_do_not_leak_across_workspaces(registry_db) -> None:
    """The settings cache is shared by every worker, so its keys must carry the workspace."""
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(org_guidelines="internal only", fable_enabled=True)
    )
    await upsert_workspace_overrides(
        "oss", WorkspaceSettingsUpdate(org_guidelines="be public", fable_enabled=False)
    )

    default_settings = await workspace_settings_cache.cached_workspace_settings("default")
    oss_settings = await workspace_settings_cache.cached_workspace_settings("oss")
    assert default_settings.org_review_guidelines == "internal only"
    assert oss_settings.org_review_guidelines == "be public"
    assert default_settings.fable_enabled is True
    assert oss_settings.fable_enabled is False
    assert (await workspace_settings_cache.cached_workspace_settings("oss"))[
        "org_guidelines"
    ] == "be public"
    assert (await workspace_settings_cache.cached_workspace_settings("default"))[
        "org_guidelines"
    ] == "internal only"


async def test_a_save_reaches_cached_reads_at_once(registry_db) -> None:
    """Saves replace the cache generation, so no reader waits out the TTL."""
    assert (await workspace_settings_cache.cached_workspace_settings("oss"))[
        "org_guidelines"
    ] is None
    assert (await workspace_settings_cache.cached_instance_settings())["org_guidelines"] is None

    await upsert_instance_settings(WorkspaceSettingsUpdate(org_guidelines="internal only"))
    assert (await workspace_settings_cache.cached_instance_settings())[
        "org_guidelines"
    ] == "internal only"
    # A workspace inherits the instance, so its cached read moves too.
    assert (await workspace_settings_cache.cached_workspace_settings("oss"))[
        "org_guidelines"
    ] == "internal only"

    await upsert_workspace_overrides("oss", WorkspaceSettingsUpdate(org_guidelines="be public"))
    assert (await workspace_settings_cache.cached_workspace_settings("oss"))[
        "org_guidelines"
    ] == "be public"


async def test_a_failed_store_read_is_not_cached(
    registry_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defaults a failed read falls back to must not reach other replicas' reads."""
    await upsert_instance_settings(WorkspaceSettingsUpdate(org_guidelines="internal only"))
    real_record = workspace_settings.instance_record

    async def unavailable() -> dict[str, object]:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(workspace_settings, "instance_record", unavailable)
    assert (await workspace_settings_cache.cached_instance_settings())["org_guidelines"] is None
    assert (await workspace_settings_cache.cached_workspace_settings("oss"))[
        "org_guidelines"
    ] is None

    monkeypatch.setattr(workspace_settings, "instance_record", real_record)
    assert (await workspace_settings_cache.cached_instance_settings())[
        "org_guidelines"
    ] == "internal only"
    assert (await workspace_settings_cache.cached_workspace_settings("oss"))[
        "org_guidelines"
    ] == "internal only"


async def test_workspace_settings_api_refuses_unknown_and_unslugifiable_names(registry_db) -> None:
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


async def test_workspace_settings_api_round_trips_overrides(registry_db) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "alice")
    await upsert_instance_settings(WorkspaceSettingsUpdate(review_draft_prs=True))

    entry = AuditLog(operation_name="put_workspace_settings")
    request = Request({"type": "http", "state": {"audit_log": entry}})
    saved = await workspace_settings.api_put_workspace_settings(
        workspace=" OSS ",
        body=WorkspaceSettingsUpdate(review_draft_prs=False),
        request=request,
        _admin={"sub": "alice"},
    )
    assert entry.workspace_id == await WORKSPACES.id_for_slug("oss")
    assert entry.enrichments.workspace == "oss"
    assert saved["overrides"] == {"review_draft_prs": False}
    assert saved["effective"]["review_draft_prs"] is False

    fetched = await workspace_settings.api_get_workspace_settings(
        workspace="oss", _session={"sub": "alice"}
    )
    assert fetched == saved
    assert (await workspace_settings.api_get_instance_settings(_session={"sub": "alice"}))[
        "review_draft_prs"
    ] is True
