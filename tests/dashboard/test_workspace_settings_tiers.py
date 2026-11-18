"""Workspace settings resolve by tier: hardcoded defaults, the instance record, then a workspace's overrides."""

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from openswe.audit_logs import tools as audit_tools
from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.models import AuditLog
from openswe.dashboard import oauth, routes, workspace_settings, workspace_settings_cache
from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    get_instance_settings,
    get_workspace_settings,
    upsert_instance_settings,
    upsert_workspace_overrides,
)
from openswe.sandboxes.lifecycle import SandboxCreateConfig
from openswe.tools import access as tool_access
from openswe.tools.access import Access
from openswe.tools.execution_defaults import configure_execution_defaults, read_execution_defaults
from openswe.tools.mcp_exposure import EXPOSED_TOOLS
from openswe.utils.json_types import JsonObject
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate, WorkspaceUpdate


async def test_an_override_applies_to_its_workspace_only(registry_db) -> None:
    assert (await get_instance_settings())["model_selection_enabled"] is False
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(
            org_guidelines="internal only", fable_enabled=True, model_selection_enabled=True
        )
    )
    view = await upsert_workspace_overrides(
        "oss",
        WorkspaceSettingsUpdate(
            org_guidelines="be public", fable_enabled=False, model_selection_enabled=False
        ),
    )
    assert view["effective"]["org_guidelines"] == "be public"
    assert view["effective"]["fable_enabled"] is False
    assert view["effective"]["model_selection_enabled"] is False
    assert view["overrides"] == {
        "org_guidelines": "be public",
        "fable_enabled": False,
        "model_selection_enabled": False,
    }
    assert (await get_workspace_settings("core"))["org_guidelines"] == "internal only"
    assert (await get_workspace_settings("core"))["model_selection_enabled"] is True
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


async def test_execution_defaults_persist_without_changing_live_environments(registry_db) -> None:
    await WORKSPACES.apply_update("default", WorkspaceUpdate(prompt="Live instructions", vcpus=2))
    environment = await WORKSPACES.get("default")
    sandbox = await SandboxCreateConfig.resolve()
    live = await get_workspace_settings()
    assert await EXECUTION_DEFAULTS.load() == ExecutionDefaults()

    await EXECUTION_DEFAULTS.update(
        ExecutionDefaults(
            prompt="  Shared instructions  ",
            repos=["acme/api", "ACME/API"],
            snapshot_id="  image:stable  ",
            vcpus=4,
            setup_script="  echo setup  ",
            create_params={"proxy_config": {"rules": []}},
        )
    )
    await asyncio.gather(
        EXECUTION_DEFAULTS.update(ExecutionDefaults(update_script="echo update")),
        EXECUTION_DEFAULTS.update(ExecutionDefaults(mem_bytes=8 * 1024**3)),
    )
    saved = await EXECUTION_DEFAULTS.load()
    assert saved.prompt == "Shared instructions"
    assert saved.repos == ["acme/api"]
    assert saved.snapshot_id == "image:stable"
    assert saved.setup_script == "echo setup"
    assert saved.update_script == "echo update"
    assert saved.mem_bytes == 8 * 1024**3
    assert saved.create_params == {"proxy_config": {"rules": []}}
    assert await WORKSPACES.get("default") == environment
    assert await SandboxCreateConfig.resolve() == sandbox
    assert await get_workspace_settings() == live

    await upsert_instance_settings(WorkspaceSettingsUpdate(org_guidelines="Live review policy"))
    await upsert_workspace_overrides("default", WorkspaceSettingsUpdate(pr_summaries=False))
    assert await EXECUTION_DEFAULTS.load() == saved
    await EXECUTION_DEFAULTS.update(ExecutionDefaults(snapshot_id=None, repos=[]))
    cleared = await EXECUTION_DEFAULTS.load()
    assert cleared.snapshot_id is None
    assert cleared.repos == []
    assert cleared.prompt == saved.prompt


@pytest.mark.parametrize("existing", [False, True])
async def test_execution_defaults_audit_tracks_concurrent_overwrites(
    registry_db, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    if existing:
        await EXECUTION_DEFAULTS.update(ExecutionDefaults(prompt="Original"))
    patch = EXECUTION_DEFAULTS.records.patch
    both_started = asyncio.Event()
    first_committed = asyncio.Event()

    async def ordered_patch(key: str, changes: JsonObject) -> tuple[JsonObject, JsonObject]:
        if changes["prompt"] == "First":
            await both_started.wait()
            saved = await patch(key, changes)
            first_committed.set()
            return saved
        both_started.set()
        await first_committed.wait()
        return await patch(key, changes)

    async def save(prompt: str) -> AuditLog:
        entry = AuditLog(operation_name="patch_execution_defaults")
        token = current_audit_log.set(entry)
        try:
            await EXECUTION_DEFAULTS.update(ExecutionDefaults(prompt=prompt))
        finally:
            current_audit_log.reset(token)
        return entry

    monkeypatch.setattr(EXECUTION_DEFAULTS.records, "patch", ordered_patch)
    async with asyncio.timeout(10):
        entries = await asyncio.gather(save("First"), save("Original"))
    for entry in entries:
        assert entry.enrichments.settings_scope == "execution"
        assert entry.enrichments.settings_changes == {
            "prompt": {"before": "[REDACTED]", "after": "[REDACTED]"}
        }
    assert (await EXECUTION_DEFAULTS.load()).prompt == "Original"
    assert (await save("Original")).enrichments.settings_changes == {}


@pytest.mark.parametrize("actor,status", [(None, 401), ("member", 403), ("admin", 200)])
async def test_execution_defaults_api_is_admin_only_and_round_trips(
    registry_db, monkeypatch: pytest.MonkeyPatch, actor: str | None, status: int
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)
    app = FastAPI()
    app.include_router(routes.router)
    if actor:
        app.dependency_overrides[oauth.require_session] = lambda: {"sub": actor}
    entry = AuditLog(operation_name="patch_execution_defaults")
    token = current_audit_log.set(entry)
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                "/dashboard/api/settings/execution",
                json={"prompt": "Private instructions", "create_params": {"region": "us"}},
            )
            assert response.status_code == status, response.text
            fetched = await client.get("/dashboard/api/settings/execution")
            assert fetched.status_code == status, fetched.text
            if status == 200:
                assert fetched.json() == response.json()
                assert fetched.json()["prompt"] == "Private instructions"
    finally:
        current_audit_log.reset(token)
    if status != 200:
        assert await EXECUTION_DEFAULTS.load() == ExecutionDefaults()
        assert entry.enrichments.settings_changes is None
    else:
        assert entry.enrichments.settings_scope == "execution"
        assert entry.enrichments.settings_changes == {
            "prompt": {"before": "[REDACTED]", "after": "[REDACTED]"},
            "create_params": {"before": "[REDACTED]", "after": "[REDACTED]"},
        }
        assert "Private instructions" not in entry.model_dump_json()
        assert "region" not in entry.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        {"slack_channel_ids": ["C123"]},
        {"vcpus": 0},
        {"create_params": {"api_key": "must-not-persist"}},
        {"create_params": {"proxy_config": {"rules": "invalid"}}},
        {
            "create_params": {
                "proxy_config": {
                    "rules": [{"headers": [{"name": "Authorization", "value": "secret"}]}]
                }
            }
        },
        {"create_params": {"access_delegation": {"permissions": "invalid"}}},
    ],
)
def test_execution_defaults_reuse_sandbox_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ExecutionDefaults.model_validate(changes)


@pytest.mark.parametrize(
    "configure",
    [configure_execution_defaults, EXPOSED_TOOLS["configure_execution_defaults"][0]],
    ids=["agent", "mcp"],
)
async def test_execution_defaults_tool_rechecks_admin_access_and_redacts_shared_results(
    registry_db,
    monkeypatch: pytest.MonkeyPatch,
    configure: Callable[[ExecutionDefaults], Awaitable[dict[str, object]]],
) -> None:
    entries: list[AuditLog] = []
    monkeypatch.setattr(audit_tools, "_enrich", AsyncMock())
    monkeypatch.setattr(audit_tools, "append_safely", AsyncMock(side_effect=entries.append))
    access = AsyncMock(return_value=Access(admin=True, admin_surface=True))
    monkeypatch.setattr(tool_access, "resolve_access", access)
    await configure(ExecutionDefaults(prompt="Private instructions"))
    assert (await read_execution_defaults())["prompt"] == "Private instructions"
    access.return_value = Access(admin=True, sole=True)
    assert await configure(ExecutionDefaults(vcpus=6)) == {
        "ok": True,
        "scope": "execution",
    }
    assert (await read_execution_defaults())["ok"] is False
    access.return_value = Access(sole=True)
    assert (await configure(ExecutionDefaults(vcpus=1)))["ok"] is False
    assert (await EXECUTION_DEFAULTS.load()).vcpus == 6
    assert [entry.operation_succeeded for entry in entries] == [True, True, False]
    assert all("Private instructions" not in entry.model_dump_json() for entry in entries)
