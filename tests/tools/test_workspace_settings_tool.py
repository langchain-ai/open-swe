"""The admin tool for the model-identity toggle rechecks authorization and writes the right tier."""

from unittest.mock import AsyncMock

import pytest

from agent.dashboard.workspace_settings import get_workspace_settings, model_identity_visible
from agent.tools import workspace_settings as settings_tool
from tests.conftest import FakeStore


@pytest.fixture(autouse=True)
def admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_tool, "require_admin", AsyncMock(return_value=None))


async def test_instance_write_applies_to_every_workspace(fake_store: FakeStore) -> None:
    result = await settings_tool.set_model_identity_visibility(False)

    assert result["ok"] is True
    assert result["workspace"] is None
    assert result["settings"]["effective"]["show_model_identity"] is False
    assert await model_identity_visible("oss") is False


async def test_workspace_write_scopes_and_clears(fake_store: FakeStore) -> None:
    await settings_tool.set_model_identity_visibility(False)
    saved = await settings_tool.set_model_identity_visibility(True, workspace="OSS")

    assert saved["ok"] is True
    assert saved["workspace"] == "oss"
    assert saved["settings"]["overrides"] == {"show_model_identity": True}
    assert await model_identity_visible("oss") is True
    assert await model_identity_visible("core") is False

    cleared = await settings_tool.set_model_identity_visibility(None, workspace="oss")
    assert cleared["settings"]["overrides"] == {}
    assert (await get_workspace_settings("oss")).show_model_identity is False


async def test_rechecks_admin(fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings_tool,
        "require_admin",
        AsyncMock(return_value="Only workspace admins can manage workspace settings."),
    )

    result = await settings_tool.set_model_identity_visibility(False)

    assert result == {
        "ok": False,
        "error": "Only workspace admins can manage workspace settings.",
    }
    assert await model_identity_visible() is True
