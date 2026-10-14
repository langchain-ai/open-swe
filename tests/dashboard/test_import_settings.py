from datetime import UTC, datetime, timedelta

import pytest

from openswe.dashboard.import_settings import import_store_settings
from openswe.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    get_workspace_settings,
    upsert_workspace_overrides,
)
from openswe.database import store_imports
from openswe.database.store_imports import StoreImport
from tests.conftest import FakeStore

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_store_import_copies_then_retires_records(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(["team_settings"], "default", {"org_guidelines": "instance"})
    fake_store.seed(["team_settings"], "legacy", {"org_guidelines": "legacy"})
    fake_store.seed(["team_settings"], "oss", {"org_guidelines": "shadowed"})
    fake_store.seed(
        ["workspace_settings"],
        "oss",
        {"org_guidelines": "store", "updated_at": "2026-01-01T00:00:00+00:00"},
    )
    await upsert_workspace_overrides("core", WorkspaceSettingsUpdate(org_guidelines="postgres"))
    fake_store.seed(["workspace_settings"], "core", {"org_guidelines": "older"})

    assert await import_store_settings() == StoreImport(moved=3, waiting=2)
    assert (await get_workspace_settings("default"))["org_guidelines"] == "instance"
    assert (await get_workspace_settings("legacy"))["org_guidelines"] == "legacy"
    assert (await get_workspace_settings("oss"))["org_guidelines"] == "store"
    assert (await get_workspace_settings("core"))["org_guidelines"] == "postgres"
    assert await import_store_settings() == StoreImport(waiting=5)

    fake_store.seed(
        ["workspace_settings"],
        "oss",
        {"org_guidelines": "old replica", "updated_at": datetime.now(UTC).isoformat()},
    )
    assert await import_store_settings() == StoreImport(moved=1, waiting=4)
    assert (await get_workspace_settings("oss"))["org_guidelines"] == "old replica"

    monkeypatch.setattr(store_imports, "RETIRE_STORE_COPY_AFTER", timedelta())
    assert await import_store_settings() == StoreImport(moved=5)
    assert await import_store_settings() == StoreImport()
    assert not fake_store.values(["team_settings"])
    assert not fake_store.values(["workspace_settings"])
