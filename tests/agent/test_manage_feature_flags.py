from collections.abc import Callable
from unittest.mock import patch

import pytest

from openswe.audit_logs import tools as audit_tools
from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.models import AuditLog
from openswe.dashboard.settings import (
    SettingsUpdate,
    get_settings,
    instance_record,
    upsert_instance_settings,
)
from openswe.dashboard.settings_records import INSTANCE_SETTINGS
from openswe.tools import access as tool_access
from openswe.tools.manage_feature_flags import manage_feature_flags

pytestmark = pytest.mark.usefixtures("registry_db")


real_resolve_access = tool_access.resolve_access


@pytest.fixture(autouse=True)
def private_admin(grant_tool_access: Callable[..., None]) -> None:
    grant_tool_access(admin=True, admin_surface=True)


@pytest.mark.usefixtures("registry_db")
async def test_instance_flags_keep_unrelated_settings_and_reset() -> None:
    await upsert_instance_settings(
        SettingsUpdate(pr_summaries=False, org_guidelines="Keep me", model_selection_enabled=True)
    )
    set_result = await manage_feature_flags(
        "set", {"pr_summaries": True, "model_selection_enabled": False}
    )
    assert set_result["effective"]["model_selection_enabled"] is False
    assert set_result["effective"]["pr_summaries"] is True
    assert (await get_settings())["org_guidelines"] == "Keep me"
    await manage_feature_flags("set", {"pr_summaries": None})
    assert (await get_settings())["pr_summaries"] is True
    assert (await get_settings())["model_selection_enabled"] is False


async def test_retired_gateway_override_is_not_returned() -> None:
    await INSTANCE_SETTINGS.put("", {"gateway_enabled": False, "pr_summaries": False})
    settings = await get_settings()
    assert "gateway_enabled" not in settings
    assert "gateway_enabled" not in await instance_record()
    assert settings["pr_summaries"] is False


@pytest.mark.parametrize(
    "flags",
    [
        {"gateway_enabled": False},
        {"review_auto_approve": True},
        {"org_guidelines": True},
        {"pr_summaries": "yes"},
        {},
    ],
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
        result = await manage_feature_flags("set", {"model_selection_enabled": True})
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
    assert await manage_feature_flags("set", {"model_selection_enabled": True}) == {
        "ok": True,
        "scope": "instance",
    }
    assert (await get_settings())["model_selection_enabled"] is True
    assert "not available in this thread" in str((await manage_feature_flags("read"))["error"])
    (entry,) = entries
    assert entry.operation_succeeded is True
    assert entry.enrichments.source == "tool"
    assert entry.enrichments.settings_scope == "instance"
    assert entry.enrichments.settings_changes == {
        "model_selection_enabled": {"before": None, "after": True}
    }
    assert current_audit_log.get() is None
