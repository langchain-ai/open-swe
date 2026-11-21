"""Structured settings preserve dashboard validation and private audience boundaries."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from openswe.agent_api.runs import run_config
from openswe.agent_api.tokens import RemoteRun
from openswe.dashboard import user_preferences
from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.dashboard.settings import (
    SettingsUpdate,
    get_instance_settings,
    upsert_instance_settings,
)
from openswe.dashboard.settings_records import INSTANCE_SETTINGS
from openswe.run_config import RunConfig
from openswe.tools import settings as module
from openswe.tools.settings import SettingsResource, patch_settings, read_settings
from openswe.users import User

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def private_admin(grant_tool_access: Callable[..., None], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "person,other")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    grant_tool_access(private=True, owner=True, admin=True, admin_surface=True, direct=True)


async def test_all_global_fields_are_discoverable_and_roundtrip() -> None:
    result = await read_settings("instance", schema_only=True)
    assert set(result["schema"]["properties"]) == set(SettingsUpdate.model_fields)
    await upsert_instance_settings(SettingsUpdate(pr_summaries=False))
    version = (await read_settings("instance"))["version"]
    await patch_settings(
        "instance",
        {"org_guidelines": "Keep this", "review_browser_domains": ["EXAMPLE.COM"]},
        version,
    )
    stored = await get_instance_settings()
    assert stored["org_guidelines"] == "Keep this"
    assert stored["pr_summaries"] is False
    assert stored["review_browser_domains"] == ["example.com"]
    version = (await read_settings("instance"))["version"]
    await patch_settings("instance", {"org_guidelines": None}, version)
    assert (await get_instance_settings())["org_guidelines"] is None


async def test_execution_schema_and_partial_write_preserve_defaults() -> None:
    assert set((await read_settings("execution", schema_only=True))["schema"]["properties"]) == set(
        ExecutionDefaults.model_fields
    )
    result = await read_settings("execution")
    await patch_settings("execution", {"prompt": "Robin guidance"}, result["version"])
    assert (await EXECUTION_DEFAULTS.load()).prompt == "Robin guidance"
    with pytest.raises(ValueError, match="Settings changed"):
        await patch_settings("execution", {"prompt": "stale"}, result["version"])


@pytest.mark.parametrize("changes", [{"not_a_setting": True}, {"pr_summaries": "yes"}, {}])
async def test_unknown_fields_and_wrong_types_are_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        await patch_settings("instance", changes, "missing")
    assert (await INSTANCE_SETTINGS.snapshot()).version == "missing"


async def test_cross_field_validation_preserved() -> None:
    with pytest.raises(ValueError):
        await patch_settings("instance", {"review_shadow_percent": 101}, "missing")
    with pytest.raises(ValueError):
        await patch_settings(
            "instance", {"review_browser_domains": ["https://example.com"]}, "missing"
        )


async def test_dashboard_write_and_racing_write_reject_stale_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await patch_settings("instance", {"pr_summaries": False}, "missing")
    version = (await INSTANCE_SETTINGS.snapshot()).version
    await upsert_instance_settings(SettingsUpdate(org_guidelines="Dashboard write"))
    with pytest.raises(ValueError, match="Settings changed"):
        await patch_settings("instance", {"pr_summaries": True}, version)
    original = INSTANCE_SETTINGS.put

    async def racing_put(
        key: str, value: dict[str, object], *, expected_version: str | None = None
    ) -> None:
        await original(key, {"org_guidelines": "concurrent"})
        await original(key, value, expected_version=expected_version)

    version = (await INSTANCE_SETTINGS.snapshot()).version
    monkeypatch.setattr(INSTANCE_SETTINGS, "put", racing_put)
    with pytest.raises(ValueError, match="Settings changed"):
        await patch_settings("instance", {"pr_summaries": True}, version)
    assert (await INSTANCE_SETTINGS.get())["org_guidelines"] == "concurrent"


async def test_shared_admin_writes_are_ack_only_and_reads_refused(
    grant_tool_access: Callable[..., None],
) -> None:
    grant_tool_access(admin=True, sole=True)
    assert await patch_settings("instance", {"org_guidelines": "private guidance"}, "missing") == {
        "ok": True,
        "resource": "instance",
        "scope": "instance",
    }
    assert (await read_settings("instance"))["ok"] is False
    assert (await read_settings("instance", schema_only=True))["ok"] is False
    grant_tool_access(admin=True)
    assert (await patch_settings("instance", {"org_guidelines": "no"}, "missing"))["ok"] is False


async def test_shared_validation_does_not_echo_stored_values(
    grant_tool_access: Callable[..., None],
) -> None:
    await INSTANCE_SETTINGS.put("", {"default_agent_model": "private-invalid-model"})
    version = (await INSTANCE_SETTINGS.snapshot()).version
    grant_tool_access(admin=True, sole=True)
    with pytest.raises(ValueError, match="Invalid settings patch") as error:
        await patch_settings("instance", {"pr_summaries": False}, version)
    assert "private-invalid-model" not in str(error.value)
    assert "default_agent_model" not in str(error.value)
    grant_tool_access(private=True, owner=True, admin=True, admin_surface=True, direct=True)
    with pytest.raises(ValueError, match="unsupported agent model"):
        await patch_settings("instance", {"pr_summaries": False}, version)


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize(
    ("resource", "patch", "field", "error_type"),
    [
        ("instance", {"pr_summaries": "private-input"}, "pr_summaries", "bool_type"),
        ("execution", {"prompt": ["private-input"]}, "prompt", "string_type"),
    ],
)
async def test_validation_details_follow_the_authorized_audience(
    grant_tool_access: Callable[..., None],
    shared: bool,
    resource: str,
    patch: dict[str, object],
    field: str,
    error_type: str,
) -> None:
    if shared:
        grant_tool_access(admin=True, sole=True)
    with pytest.raises(ValueError, match="Invalid settings patch") as error:
        await patch_settings(resource, patch, "missing")
    message = str(error.value)
    assert "private-input" not in message
    assert (field in message) is (not shared)
    assert (error_type in message) is (not shared)
    assert "Settings changed" not in message


async def test_nonadmin_toggle_and_personal_scope(
    grant_tool_access: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await User.sign_in("github", "person", login="person")
    cfg = RunConfig(github_login="person", github_linked=True)
    monkeypatch.setattr(module, "configurable", lambda: cfg)
    monkeypatch.setattr(module, "actor_is_admin", AsyncMock(return_value=False))
    grant_tool_access(private=True, owner=True, direct=True)
    with pytest.raises(ValueError, match="enable personal_settings_tools_enabled"):
        await read_settings("profile", schema_only=True)
    await INSTANCE_SETTINGS.put("", {"personal_settings_tools_enabled": True})
    with pytest.raises(ValueError, match="enable personal_settings_tools_enabled"):
        await patch_settings("profile", {"branch_prefix": "blocked/"})
    cfg = RunConfig.from_config(
        run_config(
            RemoteRun(
                thread_id="settings-run",
                assistant_id="agent",
                configurable={
                    "github_login": "person",
                    "personal_settings_tools_enabled": True,
                },
            )
        )
    )
    await patch_settings("profile", {"branch_prefix": "personal/"})
    values = (await read_settings("profile"))["values"]
    assert isinstance(values, dict)
    assert values["branch_prefix"] == "personal/"
    assert "email" not in values and "access_token" not in values
    await patch_settings("preferences", {"auto_show_changes": False})
    assert (await read_settings("preferences"))["values"]["auto_show_changes"] is False
    await patch_settings("user_preferences", {"concierge_mode": True})
    assert (await read_settings("user_preferences"))["values"]["concierge_mode"] is True
    with pytest.raises(ValueError, match="resource schema"):
        await patch_settings("user_preferences", {"proactive_offered_at": {"p": "2026-01-01"}})
    await patch_settings("instructions", {"instructions": "My instructions"})
    assert (await read_settings("instructions"))["values"]["instructions"] == "My instructions"
    assert (await read_settings("profile", user_id=user.id))["ok"] is False
    assert (await read_settings("instance", schema_only=True))["ok"] is False
    assert (await patch_settings("instance", {"personal_settings_tools_enabled": True}, "missing"))[
        "ok"
    ] is False
    cfg.personal_settings_tools_enabled = False
    with pytest.raises(ValueError, match="enable personal_settings_tools_enabled"):
        await patch_settings("profile", {"branch_prefix": "blocked/"})


async def test_failed_preference_read_does_not_write_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await User.sign_in("github", "other", login="other")
    monkeypatch.setattr(module, "actor_is_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(
        user_preferences.USER_PREFERENCES, "get", AsyncMock(side_effect=RuntimeError("down"))
    )
    writer = AsyncMock()
    monkeypatch.setattr(module, "set_user_preferences", writer)
    with pytest.raises(RuntimeError, match="down"):
        await patch_settings("preferences", {"auto_show_changes": False}, user_id=user.id)
    writer.assert_not_awaited()


async def test_admin_can_access_other_person_without_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await User.sign_in("github", "other", login="other")
    monkeypatch.setattr(module, "actor_is_admin", AsyncMock(return_value=True))
    await patch_settings("instructions", {"instructions": "User guidance"}, user_id=user.id)
    assert (await read_settings("instructions", user_id=user.id))["values"][
        "instructions"
    ] == "User guidance"


def test_all_resource_schemas_use_model_fields() -> None:
    for resource in SettingsResource:
        assert set(resource.schema()["properties"]) == resource.fields
        assert resource.schema()["additionalProperties"] is False


async def test_unlinked_person_cannot_use_a_stale_owner_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person = await User.sign_in("github", "person", login="person")
    monkeypatch.setattr(module, "actor_is_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(
        module,
        "configurable",
        lambda: RunConfig(
            user_id=str(person.id),
            github_login="stale-owner",
            github_linked=False,
            personal_settings_tools_enabled=True,
        ),
    )
    await patch_settings("instructions", {"instructions": "Own private guidance"})
    result = await read_settings("instructions")
    assert result["user_id"] == str(person.id)
    assert result["values"] == {"instructions": "Own private guidance"}
