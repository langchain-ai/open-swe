from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    get_workspace_settings,
    upsert_instance_settings,
)
from agent.tools.manage_feature_flags import manage_feature_flags
from agent.workspaces.store import WORKSPACES, Workspace
from tests.conftest import FakeStore


async def test_instance_flags_keep_unrelated_settings_and_reset(
    fake_store: FakeStore,
) -> None:
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(pr_summaries=False, org_guidelines="Keep me", gateway_enabled=True)
    )
    with patch(
        "agent.tools.manage_feature_flags.require_private_admin_surface",
        AsyncMock(return_value=None),
    ):
        set_result = await manage_feature_flags(
            "set", {"pr_summaries": True, "gateway_enabled": False}
        )
        assert set_result["effective"]["gateway_enabled"] is False
        assert set_result["effective"]["pr_summaries"] is True
        assert (await get_workspace_settings())["org_guidelines"] == "Keep me"
        await manage_feature_flags("set", {"pr_summaries": None})
        assert (await get_workspace_settings())["pr_summaries"] is True
        assert (await get_workspace_settings())["gateway_enabled"] is False


async def test_workspace_flags_inherit_and_keep_other_overrides(fake_store: FakeStore) -> None:
    fake_store.seed(
        ["workspace_settings"], "team", {"org_guidelines": "Keep me", "pr_summaries": False}
    )
    with (
        patch.object(
            WORKSPACES,
            "get",
            AsyncMock(return_value=Workspace(name="team", slug="team", prompt="")),
        ),
        patch(
            "agent.tools.manage_feature_flags.require_private_admin_surface",
            AsyncMock(return_value=None),
        ),
    ):
        assert (await manage_feature_flags("read", workspace="team"))["overrides"] == {
            "pr_summaries": False
        }
        await manage_feature_flags("set", {"pr_summaries": True}, workspace="team")
        assert (await get_workspace_settings("team"))["pr_summaries"] is True
        result = await manage_feature_flags("set", {"pr_summaries": None}, workspace="team")
        assert result["overrides"] == {}
        assert (await get_workspace_settings("team"))["pr_summaries"] is True
        assert fake_store.values(["workspace_settings"])["team"]["org_guidelines"] == "Keep me"


@pytest.mark.parametrize("flags", [{"review_auto_approve": True}, {"pr_summaries": "yes"}, {}])
async def test_invalid_flags_do_not_write(fake_store: FakeStore, flags: dict[str, object]) -> None:
    before = deepcopy(fake_store.items)
    with (
        patch(
            "agent.tools.manage_feature_flags.require_private_admin_surface",
            AsyncMock(return_value=None),
        ),
        pytest.raises(ValueError),
    ):
        await manage_feature_flags("set", flags)
    assert fake_store.items == before


@pytest.mark.parametrize(
    "config",
    [
        {"source": "dashboard", "github_login": "admin"},
        {"admin_thread": True, "source": "slack", "github_login": "admin"},
        {
            "admin_thread": True,
            "source": "slack",
            "github_login": "admin",
            "slack_thread": {
                "channel_id": "C123",
                "thread_ts": "1700000000.000100",
                "channel_context": {"is_im": False},
            },
        },
        {"admin_thread": True, "source": "schedule", "github_login": "admin"},
        {"admin_thread": True, "source": "dashboard", "github_login": "not-admin"},
    ],
)
async def test_private_admin_gate_rejects_other_surfaces(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch, config: dict[str, object]
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    with (
        patch("agent.run_config.get_config", return_value={"configurable": config}),
        pytest.raises(ValueError, match="private admin surface"),
    ):
        await manage_feature_flags("set", {"gateway_enabled": True})
    assert not fake_store.items


async def test_non_admin_cannot_read_or_write_flags(fake_store: FakeStore) -> None:
    with (
        patch(
            "agent.tools.manage_feature_flags.require_private_admin_surface",
            AsyncMock(return_value="Private admin required"),
        ),
        pytest.raises(ValueError, match="Private admin"),
    ):
        await manage_feature_flags("set", {"gateway_enabled": True})
    assert not fake_store.items
