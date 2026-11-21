"""Global defaults, shared cache invalidation and admin-only writes."""

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from openswe.audit_logs import tools as audit_tools
from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.models import AuditLog
from openswe.dashboard import oauth, routes, settings, settings_cache
from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.dashboard.settings import SettingsUpdate, upsert_instance_settings
from openswe.tools import access as tool_access
from openswe.tools.access import Access
from openswe.tools.execution_defaults import configure_execution_defaults, read_execution_defaults
from openswe.tools.mcp_exposure import EXPOSED_TOOLS
from openswe.users.repository_defaults import RepositoryDefaults
from openswe.utils.json_types import JsonObject


async def test_a_save_reaches_cached_reads_at_once(registry_db) -> None:
    assert (await settings_cache.cached_settings()).org_review_guidelines is None
    await upsert_instance_settings(SettingsUpdate(org_guidelines="Shared review policy"))
    assert (await settings_cache.cached_settings()).org_review_guidelines == "Shared review policy"
    await upsert_instance_settings(SettingsUpdate(org_guidelines="Updated policy"))
    assert (await settings_cache.cached_settings()).org_review_guidelines == "Updated policy"


async def test_a_failed_store_read_is_not_cached(
    registry_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    await upsert_instance_settings(SettingsUpdate(org_guidelines="Shared review policy"))
    real_record = settings.instance_record
    monkeypatch.setattr(
        settings, "instance_record", AsyncMock(side_effect=RuntimeError("unavailable"))
    )
    assert (await settings_cache.cached_settings()).org_review_guidelines is None
    monkeypatch.setattr(settings, "instance_record", real_record)
    assert (await settings_cache.cached_settings()).org_review_guidelines == "Shared review policy"


def test_historical_scoped_repository_defaults_are_ignored() -> None:
    personal = RepositoryDefaults.model_validate(
        {
            "default_repo": "acme/global",
            "workspace_default_repos": {"oss": "acme/private"},
        }
    )
    assert personal.repo_config() == {"owner": "acme", "name": "global"}
    assert (
        RepositoryDefaults.model_validate(
            {
                "workspace_default_repos": {"default": "acme/private"},
            }
        ).repo_config()
        is None
    )


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
                json={
                    "prompt": "Private instructions",
                    "create_params": {"run_config": {"env_vars": {"REGION": "us"}}},
                },
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
        assert "REGION" not in entry.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        {"slack_channel_ids": ["C123"]},
        {"vcpus": 0},
        {"create_params": {"api_key": "must-not-persist"}},
        {"create_params": {"region": "us"}},
        {"create_params": {"labels": {"team": "infra"}}},
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
    monkeypatch.setattr(AuditLog, "enrich_tool", AsyncMock())
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
    saved = await EXECUTION_DEFAULTS.load()
    assert saved.vcpus == 6
    assert [entry.operation_succeeded for entry in entries] == [True, True, False]
    assert all("Private instructions" not in entry.model_dump_json() for entry in entries)
