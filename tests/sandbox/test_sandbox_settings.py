from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import store as agent_store
from agent.settings.sandbox_settings import (
    SandboxSettingsUpdate,
    get_admin_base_snapshot_id,
    get_sandbox_settings,
    resolve_base_snapshot_id,
    upsert_sandbox_settings,
)


def _store(item: object) -> MagicMock:
    client = MagicMock()
    client.store.get_item = AsyncMock(return_value=item)
    client.store.put_item = AsyncMock()
    return client


def test_update_normalizes_blank_to_none() -> None:
    assert SandboxSettingsUpdate(base_snapshot_id="  ").base_snapshot_id is None
    assert SandboxSettingsUpdate(base_snapshot_id=" snap-1 ").base_snapshot_id == "snap-1"


def test_update_rejects_oversized_value() -> None:
    with pytest.raises(ValueError, match="at most"):
        SandboxSettingsUpdate(base_snapshot_id="x" * 513)


async def test_admin_value_wins_over_env() -> None:
    with (
        patch.object(
            agent_store,
            "store_client",
            return_value=_store({"value": {"base_snapshot_id": "admin-snap"}}),
        ),
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-snap"}, clear=True),
    ):
        assert await get_admin_base_snapshot_id() == "admin-snap"
        assert await resolve_base_snapshot_id() == "admin-snap"


async def test_falls_back_to_env_without_record() -> None:
    with (
        patch.object(agent_store, "store_client", return_value=_store(None)),
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-snap"}, clear=True),
    ):
        assert await get_admin_base_snapshot_id() is None
        assert await resolve_base_snapshot_id() == "env-snap"


async def test_store_failure_falls_back_to_env() -> None:
    client = MagicMock()
    client.store.get_item = AsyncMock(side_effect=RuntimeError("store down"))
    with (
        patch.object(agent_store, "store_client", return_value=client),
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-snap"}, clear=True),
    ):
        assert await resolve_base_snapshot_id() == "env-snap"


async def test_settings_report_source() -> None:
    with (
        patch.object(agent_store, "store_client", return_value=_store(None)),
        patch.dict("os.environ", {}, clear=True),
    ):
        unset = await get_sandbox_settings()
    assert unset["base_snapshot_source"] == "unset"
    assert unset["effective_base_snapshot_id"] is None

    with (
        patch.object(agent_store, "store_client", return_value=_store(None)),
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-snap"}, clear=True),
    ):
        from_env = await get_sandbox_settings()
    assert from_env["base_snapshot_source"] == "env"
    assert from_env["effective_base_snapshot_id"] == "env-snap"


async def test_upsert_persists_and_returns_effective() -> None:
    client = _store({"value": {"base_snapshot_id": "admin-snap"}})
    with (
        patch.object(agent_store, "store_client", return_value=client),
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-snap"}, clear=True),
    ):
        result = await upsert_sandbox_settings(
            SandboxSettingsUpdate(base_snapshot_id="admin-snap"), updated_by="octo"
        )
    stored = client.store.put_item.await_args.args[2]
    assert stored["base_snapshot_id"] == "admin-snap"
    assert stored["updated_by"] == "octo"
    assert result["base_snapshot_source"] == "admin"
    assert result["effective_base_snapshot_id"] == "admin-snap"


async def test_runtime_prefers_repo_snapshot_then_admin_base() -> None:
    from agent.runtime import sandbox

    with (
        patch.object(
            sandbox, "resolve_repo_snapshot_id", new_callable=AsyncMock, return_value="repo-snap"
        ),
        patch.object(
            sandbox, "get_admin_base_snapshot_id", new_callable=AsyncMock, return_value="admin-snap"
        ),
    ):
        assert (
            await sandbox._resolve_snapshot_id({"owner": "acme", "name": "repo"}, None)
            == "repo-snap"
        )

    with (
        patch.object(
            sandbox, "resolve_repo_snapshot_id", new_callable=AsyncMock, return_value=None
        ),
        patch.object(
            sandbox, "get_admin_base_snapshot_id", new_callable=AsyncMock, return_value="admin-snap"
        ),
    ):
        assert (
            await sandbox._resolve_snapshot_id({"owner": "acme", "name": "repo"}, None)
            == "admin-snap"
        )
        assert await sandbox._resolve_snapshot_id(None, None) == "admin-snap"
