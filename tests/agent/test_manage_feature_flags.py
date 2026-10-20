from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest

from openswe.audit_logs import tools as audit_tools
from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.models import AuditLog
from openswe.dashboard.settings_records import WORKSPACE_SETTINGS
from openswe.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    get_workspace_settings,
    upsert_instance_settings,
)
from openswe.tools import access as tool_access
from openswe.tools.manage_feature_flags import manage_feature_flags
from openswe.workspaces.store import WORKSPACES, Workspace

real_resolve_access = tool_access.resolve_access


@pytest.fixture(autouse=True)
def private_admin(grant_tool_access: Callable[..., None]) -> None:
    grant_tool_access(admin=True, admin_surface=True)


@pytest.mark.usefixtures("registry_db")
async def test_instance_flags_keep_unrelated_settings_and_reset() -> None:
    await upsert_instance_settings(
        WorkspaceSettingsUpdate(
            review_trace_links=False, org_guidelines="Keep me", gateway_enabled=True
        )
    )
    set_result = await manage_feature_flags(
        "set", {"review_trace_links": True, "gateway_enabled": False}
    )
    assert set_result["effective"]["gateway_enabled"] is False
    assert set_result["effective"]["review_trace_links"] is True
    assert (await get_workspace_settings())["org_guidelines"] == "Keep me"
    await manage_feature_flags("set", {"review_trace_links": None})
    assert (await get_workspace_settings())["review_trace_links"] is True
    assert (await get_workspace_settings())["gateway_enabled"] is False


@pytest.mark.usefixtures("registry_db")
async def test_workspace_flags_inherit_and_keep_other_overrides() -> None:
    await WORKSPACE_SETTINGS.put("team", {"org_guidelines": "Keep me", "review_trace_links": False})
    with (
        patch.object(
            WORKSPACES,
            "get",
            AsyncMock(return_value=Workspace(name="team", slug="team", prompt="")),
        ),
    ):
        assert (await manage_feature_flags("read", workspace="team"))["overrides"] == {
            "review_trace_links": False
        }
        await manage_feature_flags("set", {"review_trace_links": True}, workspace="team")
        assert (await get_workspace_settings("team"))["review_trace_links"] is True
        result = await manage_feature_flags("set", {"review_trace_links": None}, workspace="team")
        assert result["overrides"] == {}
        assert (await get_workspace_settings("team"))["review_trace_links"] is True
        assert (await WORKSPACE_SETTINGS.get("team") or {})["org_guidelines"] == "Keep me"


@pytest.mark.parametrize(
    "flags",
    [{"review_auto_approve": True}, {"org_guidelines": True}, {"review_trace_links": "yes"}, {}],
)
async def test_invalid_flags_are_refused(flags: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        await manage_feature_flags("set", flags)


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
    monkeypatch: pytest.MonkeyPatch, config: dict[str, object]
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    monkeypatch.setattr(tool_access, "resolve_access", real_resolve_access)
    with patch("openswe.run_config.get_config", return_value={"configurable": config}):
        result = await manage_feature_flags("set", {"gateway_enabled": True})
    assert "not available in this thread" in str(result["error"])


@pytest.mark.usefixtures("registry_db")
async def test_sole_writer_sets_flags_but_cannot_read_them(
    grant_tool_access: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    entries: list[AuditLog] = []

    async def append(entry: AuditLog) -> None:
        entries.append(entry)

    monkeypatch.setattr(audit_tools, "append_safely", append)
    grant_tool_access(admin=True, sole=True)
    assert await manage_feature_flags("set", {"gateway_enabled": True}) == {
        "ok": True,
        "scope": "instance",
    }
    assert (await get_workspace_settings())["gateway_enabled"] is True
    assert "not available in this thread" in str((await manage_feature_flags("read"))["error"])
    (entry,) = entries
    assert entry.operation_succeeded is True
    assert entry.enrichments.source == "tool"
    assert entry.enrichments.settings_scope == "instance"
    assert entry.enrichments.settings_changes == {
        "gateway_enabled": {"before": None, "after": True}
    }
    assert current_audit_log.get() is None
