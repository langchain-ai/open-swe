from collections.abc import Callable
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import langgraph_sdk
import pytest

from openswe.dashboard.personal_settings import SettingValue, patch_personal_settings
from openswe.dashboard.user_preferences import USER_PREFERENCES
from openswe.tools.read_user_settings import read_user_settings
from openswe.tools.save_user_settings import save_user_settings
from tests.conftest import FakeUserRecords


@pytest.fixture
def requester(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    configurable: dict[str, object] = {
        "thread_id": "thread-1",
        "source": "dashboard",
        "github_login": "Alice",
    }
    for module in (
        "openswe.run_config",
        "openswe.tools.save_user_settings",
        "openswe.tools.read_user_settings",
    ):
        monkeypatch.setattr(
            import_module(module), "get_config", lambda: {"configurable": configurable}
        )
    return configurable


@pytest.fixture
def saved_scope(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    metadata: dict[str, object] = {
        "visibility": "private",
        "owner_type": "user",
        "owner_login": "alice",
        "participant_logins": ["alice", "bob"],
    }
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(side_effect=lambda _id: {"metadata": metadata}))
        ),
    )
    return metadata


@pytest.mark.parametrize("source", ["dashboard", "slack"])
async def test_private_requester_partial_update_preserves_other_settings_and_users(
    user_records: FakeUserRecords,
    requester: dict[str, object],
    saved_scope: dict[str, object],
    source: str,
) -> None:
    requester["source"] = source
    profile = {
        "default_model": "openai:gpt-6.1-sol",
        "reasoning_effort": "high",
        "default_subagent_model": "anthropic:claude-haiku-4-5",
        "subagent_reasoning_effort": "none",
        "default_repo": "org/private",
        "branch_prefix": "alice/",
        "base_branch": "develop",
        "draft_prs": False,
        "review_draft_prs": True,
        "model_routing_enabled": True,
        "recent_thread_context_enabled": True,
        "email": "alice@example.com",
    }
    prefs = {
        "default_visibility": "public",
        "local_tracing_project": "keep-project",
        "default_workspace": "keep-workspace",
    }
    user_records.seed("profile", "Alice", profile)
    user_records.seed("profile", "bob", {"draft_prs": True})
    user_records.seed("dashboard_preferences", "Alice", prefs)
    user_records.seed("github_oauth_token", "Alice", {"encrypted_gh_token": "secret"})
    result = await save_user_settings({"auto_fix_ci": False, "default_workspace": "  NEW  "})
    assert result == {
        "ok": True,
        "login": "Alice",
        "updated": {"auto_fix_ci": False, "default_workspace": "new"},
    }
    saved = user_records.get("profile", "Alice")
    assert {key: saved[key] for key in profile} == profile
    assert saved["auto_fix_ci"] is False
    assert user_records.get("profile", "bob") == {"draft_prs": True}
    assert user_records.get("github_oauth_token", "Alice") == {"encrypted_gh_token": "secret"}
    saved_prefs = user_records.get("dashboard_preferences", "Alice")
    assert {key: saved_prefs[key] for key in prefs} == {**prefs, "default_workspace": "new"}


@pytest.mark.parametrize(
    ("config_patch", "metadata_patch"),
    [
        ({"github_login": None, "user_email": "alice@example.com"}, {}),
        ({"github_login": "bob"}, {}),
        ({"thread_id": None}, {}),
        ({"visibility": "private", "owner_login": "Alice"}, {"visibility": "public"}),
        ({}, {"owner_login": ""}),
        ({}, {"visibility": "unknown"}),
        ({}, {"owner_type": "system"}),
        ({"background_task_completion": True}, {}),
        ({"schedule_id": "schedule-1"}, {}),
        ({"watch_key": "watch-1"}, {}),
        ({"source": "incidents_agent"}, {}),
        ({"source": "github"}, {}),
        ({"source": None}, {}),
    ],
)
async def test_unauthorized_calls_never_read_or_write_settings(
    requester: dict[str, object],
    saved_scope: dict[str, object],
    config_patch: dict[str, object],
    metadata_patch: dict[str, object],
) -> None:
    requester.update(config_patch)
    saved_scope.update(metadata_patch)
    with patch(
        "openswe.tools.save_user_settings.patch_personal_settings", new_callable=AsyncMock
    ) as save:
        result = await save_user_settings({"draft_prs": False})
    assert result["ok"] is False
    save.assert_not_awaited()


async def test_unavailable_thread_scope_fails_closed(
    monkeypatch: pytest.MonkeyPatch, requester: dict[str, object]
) -> None:
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(threads=SimpleNamespace(get=AsyncMock(side_effect=TimeoutError))),
    )
    with patch(
        "openswe.tools.save_user_settings.patch_personal_settings", new_callable=AsyncMock
    ) as save:
        assert (await save_user_settings({"draft_prs": False}))["ok"] is False
    save.assert_not_awaited()


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"login": "bob"},
        {"encrypted_gh_token": "secret"},
        {"admin": True},
        {"default_model": "unknown-model"},
        {"default_model": "openai:gpt-5.5"},
        {"default_model": "anthropic:claude-fable-5-1"},
        {"default_subagent_model": None, "subagent_reasoning_effort": "high"},
        {"branch_prefix": "new/", "default_visibility": "invalid"},
        {"preserve_sandbox_memory": None, "auto_fix_ci": False},
    ],
)
async def test_invalid_patch_rejects_all_changes(
    user_records: FakeUserRecords,
    requester: dict[str, object],
    saved_scope: dict[str, object],
    settings: dict[str, SettingValue],
) -> None:
    user_records.seed(
        "profile", "Alice", {"default_model": "openai:gpt-6.1-sol", "reasoning_effort": "high"}
    )
    before = deepcopy(user_records.items)
    assert (await save_user_settings(settings))["ok"] is False
    assert user_records.items == before


@pytest.mark.parametrize("value", [True, False, None])
@pytest.mark.parametrize("mixed", [False, True])
async def test_agent_cannot_change_concierge_mode_even_in_mixed_patch(
    user_records: FakeUserRecords,
    requester: dict[str, object],
    saved_scope: dict[str, object],
    value: bool | None,
    mixed: bool,
) -> None:
    user_records.seed("profile", "Alice", {"auto_fix_ci": True})
    user_records.seed("dashboard_preferences", "Alice", {"default_workspace": "keep"})
    settings: dict[str, SettingValue] = {"concierge_mode": value}
    if mixed:
        settings = {"auto_fix_ci": False, "default_workspace": "new", **settings}
    before = deepcopy(user_records.items)
    result = await save_user_settings(settings)
    assert result["ok"] is False
    assert "dashboard" in str(result["error"])
    assert user_records.items == before


async def test_sandbox_memory_flag_requires_user_and_validates_before_writing(
    user_records: FakeUserRecords, requester: dict[str, object], saved_scope: dict[str, object]
) -> None:
    from openswe.users import User, UserPreferences

    with patch.object(
        User, "update_preferences", new_callable=AsyncMock, return_value=None
    ) as save:
        assert (await save_user_settings({"preserve_sandbox_memory": True, "auto_fix_ci": False}))[
            "ok"
        ] is False
        save.assert_awaited_once()
    assert not user_records.items
    with patch.object(
        User,
        "update_preferences",
        new_callable=AsyncMock,
        return_value=UserPreferences(preserve_sandbox_memory=True),
    ) as save:
        assert (await save_user_settings({"preserve_sandbox_memory": True, "auto_fix_ci": False}))[
            "updated"
        ] == {"preserve_sandbox_memory": True, "auto_fix_ci": False}
        assert save.await_args.args[0] == "Alice"
        assert save.await_args.args[1].preserve_sandbox_memory is True
    assert user_records.get("profile", "Alice")["auto_fix_ci"] is False


async def test_first_setting_does_not_pin_inherited_model_defaults(
    user_records: FakeUserRecords,
) -> None:
    await patch_personal_settings("alice", {"auto_fix_ci": False})
    profile = user_records.get("profile", "alice")
    assert profile["auto_fix_ci"] is False
    assert "default_model" not in profile
    assert "reasoning_effort" not in profile
    await patch_personal_settings("alice", {"local_tracing_project": "  project  "})
    prefs = user_records.get("dashboard_preferences", "alice")
    assert prefs["default_visibility"] == "private"
    assert prefs["local_tracing_project"] == "project"


async def test_preference_read_failure_does_not_overwrite_saved_defaults(
    user_records: FakeUserRecords,
) -> None:
    with patch.object(USER_PREFERENCES, "get", side_effect=TimeoutError):
        with pytest.raises(TimeoutError):
            await patch_personal_settings("alice", {"draft_prs": False, "default_workspace": "new"})
    assert not user_records.items


async def test_private_read_exposes_all_ordinary_settings_only_for_requester(
    user_records: FakeUserRecords, requester: dict[str, object], saved_scope: dict[str, object]
) -> None:
    ordinary = {
        "default_repo": "org/private",
        "base_branch": "develop",
        "branch_prefix": "alice/",
        "model_routing_enabled": False,
    }
    user_records.seed(
        "profile",
        "Alice",
        {**ordinary, "email": "private@example.com", "encrypted_gh_token": "secret"},
    )
    user_records.seed(
        "dashboard_preferences",
        "Alice",
        {
            "default_workspace": "mine",
            "default_visibility": "private",
            "local_tracing_project": "tracing",
        },
    )
    user_records.seed("profile", "bob", {"default_repo": "org/bob"})
    result = await read_user_settings()
    assert result["participants"] == [
        {
            "login": "Alice",
            "profile": {
                **ordinary,
                "concierge_mode": False,
                "preserve_sandbox_memory": True,
                "pr_review_links": False,
                "pr_failure_reactions": False,
                "prefer_tools_in_sandbox": False,
                "experimental_task_coordination": False,
            },
            "preferences": {
                "default_workspace": "mine",
                "default_visibility": "private",
                "local_tracing_project": "tracing",
                "follow_up_behavior": "steer",
            },
            "instructions": "",
        }
    ]
    assert "secret" not in repr(result)
    assert "private@example.com" not in repr(result)
    assert "bob" not in repr(result)


@pytest.mark.parametrize("login", [None, "bob"])
async def test_private_read_rejects_unverified_requesters(
    requester: dict[str, object], saved_scope: dict[str, object], login: str | None
) -> None:
    requester["github_login"] = login
    with patch("openswe.tools.read_user_settings.get_profile", new_callable=AsyncMock) as profile:
        assert "error" in await read_user_settings()
    profile.assert_not_awaited()


async def test_sole_writer_save_does_not_publish_stored_settings(
    user_records: FakeUserRecords,
    requester: dict[str, object],
    grant_tool_access: Callable[..., None],
) -> None:
    grant_tool_access(sole=True, direct=True)
    user_records.seed(
        "profile",
        "Alice",
        {"default_model": "openai:gpt-6-sol", "reasoning_effort": "secret-effort"},
    )
    result = await save_user_settings({"default_model": "anthropic:claude-opus-5-5"})
    assert result == {"ok": True}
    assert user_records.get("profile", "Alice")["default_model"] == "anthropic:claude-opus-5-5"
