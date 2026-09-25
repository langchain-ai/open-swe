from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import langgraph_sdk
import pytest
from langchain_core.tools import StructuredTool

from agent.dashboard.personal_settings import SettingValue, patch_personal_settings
from agent.tools.read_user_settings import read_user_settings
from agent.tools.save_user_settings import save_user_settings
from tests.conftest import FakeStore


@pytest.fixture
def requester(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    configurable: dict[str, object] = {
        "thread_id": "thread-1",
        "source": "dashboard",
        "github_login": "Alice",
    }
    monkeypatch.setattr(
        import_module("agent.tools.save_user_settings"),
        "get_config",
        lambda: {"configurable": configurable},
    )
    monkeypatch.setattr(
        import_module("agent.tools.read_user_settings"),
        "get_config",
        lambda: {"configurable": configurable},
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
    fake_store: FakeStore,
    requester: dict[str, object],
    saved_scope: dict[str, object],
    source: str,
) -> None:
    requester["source"] = source
    profile = {
        "default_model": "openai:gpt-6-sol",
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
    fake_store.seed(["profiles"], "Alice", profile)
    fake_store.seed(["profiles"], "bob", {"draft_prs": True})
    fake_store.seed(["user_preferences"], "Alice", prefs)
    fake_store.seed(["oauth_tokens"], "Alice", {"encrypted_gh_token": "secret"})
    result = await save_user_settings({"auto_fix_ci": False, "default_workspace": "  NEW  "})
    assert result == {
        "ok": True,
        "login": "Alice",
        "updated": {"auto_fix_ci": False, "default_workspace": "new"},
    }
    saved = fake_store.values(["profiles"])["Alice"]
    assert {key: saved[key] for key in profile} == profile
    assert saved["auto_fix_ci"] is False
    assert fake_store.values(["profiles"])["bob"] == {"draft_prs": True}
    assert fake_store.values(["oauth_tokens"])["Alice"] == {"encrypted_gh_token": "secret"}
    saved_prefs = fake_store.values(["user_preferences"])["Alice"]
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
        "agent.tools.save_user_settings.patch_personal_settings", new_callable=AsyncMock
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
        "agent.tools.save_user_settings.patch_personal_settings", new_callable=AsyncMock
    ) as save:
        assert (await save_user_settings({"draft_prs": False}))["ok"] is False
    save.assert_not_awaited()


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"login": "bob"},
        {"email": "bob@example.com"},
        {"encrypted_gh_token": "secret"},
        {"notion_token": "secret"},
        {"admin": True},
        {"gateway_enabled": True},
        {"instructions": "new"},
        {"theme": "dark"},
        {"default_visibility": "everyone"},
        {"concierge_mode": None},
        {"default_model": "unknown-model"},
        {"default_model": "openai:gpt-5.5"},
        {"default_subagent_model": "openai:gpt-5.5", "subagent_reasoning_effort": "high"},
        {"default_model": "anthropic:claude-fable-5-1"},
        {"default_model": "anthropic:claude-haiku-4-5", "reasoning_effort": "high"},
        {"default_subagent_model": None, "subagent_reasoning_effort": "high"},
        {
            "default_subagent_model": "anthropic:claude-haiku-4-5",
            "subagent_reasoning_effort": "high",
        },
        {"branch_prefix": "new/", "default_visibility": "invalid"},
    ],
)
async def test_invalid_patch_rejects_all_changes(
    fake_store: FakeStore,
    requester: dict[str, object],
    saved_scope: dict[str, object],
    settings: dict[str, SettingValue],
) -> None:
    fake_store.seed(
        ["profiles"], "Alice", {"default_model": "openai:gpt-6-sol", "reasoning_effort": "high"}
    )
    before = deepcopy(fake_store.items)
    assert (await save_user_settings(settings))["ok"] is False
    assert fake_store.items == before


@pytest.mark.parametrize("value", [True, False, None])
@pytest.mark.parametrize("mixed", [False, True])
async def test_agent_cannot_change_concierge_mode_even_in_mixed_patch(
    fake_store: FakeStore,
    requester: dict[str, object],
    saved_scope: dict[str, object],
    value: bool | None,
    mixed: bool,
) -> None:
    fake_store.seed(["profiles"], "Alice", {"auto_fix_ci": True})
    fake_store.seed(["user_preferences"], "Alice", {"default_workspace": "keep"})
    settings: dict[str, SettingValue] = {"concierge_mode": value}
    if mixed:
        settings = {"auto_fix_ci": False, "default_workspace": "new", **settings}
    before = deepcopy(fake_store.items)
    result = await save_user_settings(settings)
    assert result["ok"] is False
    assert "dashboard" in str(result["error"])
    assert fake_store.items == before


async def test_nullable_fields_clear_and_false_values_survive(fake_store: FakeStore) -> None:
    fake_store.seed(
        ["profiles"],
        "alice",
        {
            "default_model": "openai:gpt-6-sol",
            "reasoning_effort": "high",
            "default_subagent_model": "anthropic:claude-haiku-4-5",
            "subagent_reasoning_effort": "none",
            "review_draft_prs": True,
            "model_routing_enabled": True,
            "default_repo": "org/repo",
            "base_branch": "develop",
            "branch_prefix": "alice/",
        },
    )
    patch_values: dict[str, SettingValue] = {
        "default_subagent_model": None,
        "subagent_reasoning_effort": None,
        "review_draft_prs": None,
        "model_routing_enabled": None,
        "default_repo": None,
        "base_branch": None,
        "branch_prefix": None,
        "auto_fix_ci": False,
        "draft_prs": False,
        "default_visibility": "private",
        "local_tracing_project": None,
        "default_workspace": None,
    }
    assert await patch_personal_settings("alice", patch_values) == patch_values
    saved = {
        **fake_store.values(["profiles"])["alice"],
        **fake_store.values(["user_preferences"])["alice"],
    }
    assert {key: saved[key] for key in patch_values} == patch_values


async def test_first_setting_does_not_pin_inherited_model_defaults(fake_store: FakeStore) -> None:
    await patch_personal_settings("alice", {"auto_fix_ci": False})
    profile = fake_store.values(["profiles"])["alice"]
    assert profile["auto_fix_ci"] is False
    assert "default_model" not in profile
    assert "reasoning_effort" not in profile
    await patch_personal_settings("alice", {"local_tracing_project": "  project  "})
    prefs = fake_store.values(["user_preferences"])["alice"]
    assert prefs["default_visibility"] == "private"
    assert prefs["local_tracing_project"] == "project"


@pytest.mark.parametrize("model", ["openai:gpt-5.5", "anthropic:claude-fable-5-1"])
async def test_unrelated_patch_preserves_retired_model_pairs(
    fake_store: FakeStore, model: str
) -> None:
    profile = {
        "default_model": model,
        "reasoning_effort": "high",
        "default_subagent_model": model,
        "subagent_reasoning_effort": "high",
        "default_repo": "org/repo",
    }
    fake_store.seed(["profiles"], "alice", profile)
    settings: dict[str, SettingValue] = {
        "draft_prs": False,
        "branch_prefix": "new/",
        "auto_fix_ci": False,
    }
    assert await patch_personal_settings("alice", settings) == settings
    saved = fake_store.values(["profiles"])["alice"]
    assert {key: saved[key] for key in profile} == profile
    assert {key: saved[key] for key in settings} == settings


async def test_model_effort_patch_uses_dashboard_normalization(fake_store: FakeStore) -> None:
    fake_store.seed(
        ["profiles"],
        "alice",
        {"default_model": "anthropic:claude-opus-old", "reasoning_effort": "high"},
    )
    await patch_personal_settings("alice", {"reasoning_effort": "low"})
    profile = fake_store.values(["profiles"])["alice"]
    assert profile["default_model"] == "anthropic:claude-opus-5-5"
    assert profile["reasoning_effort"] == "low"


async def test_preference_read_failure_does_not_overwrite_saved_defaults(
    fake_store: FakeStore,
) -> None:
    with patch("agent.dashboard.personal_settings.get_value", side_effect=TimeoutError):
        with pytest.raises(TimeoutError):
            await patch_personal_settings("alice", {"draft_prs": False, "default_workspace": "new"})
    assert not fake_store.items


async def test_private_read_exposes_all_ordinary_settings_only_for_requester(
    fake_store: FakeStore, requester: dict[str, object], saved_scope: dict[str, object]
) -> None:
    ordinary = {
        "default_repo": "org/private",
        "base_branch": "develop",
        "branch_prefix": "alice/",
        "model_routing_enabled": False,
    }
    fake_store.seed(
        ["profiles"],
        "Alice",
        {**ordinary, "email": "private@example.com", "encrypted_gh_token": "secret"},
    )
    fake_store.seed(
        ["user_preferences"],
        "Alice",
        {
            "default_workspace": "mine",
            "default_visibility": "private",
            "local_tracing_project": "tracing",
        },
    )
    fake_store.seed(["profiles"], "bob", {"default_repo": "org/bob"})
    result = await read_user_settings()
    assert result["participants"] == [
        {
            "login": "Alice",
            "profile": {**ordinary, "concierge_mode": False},
            "preferences": {
                "default_workspace": "mine",
                "default_visibility": "private",
                "local_tracing_project": "tracing",
                "follow_up_behavior": "steer",
            },
            "instructions": "",
            "connections": {"notion": {"connected": False}},
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
    with patch("agent.tools.read_user_settings.get_profile", new_callable=AsyncMock) as profile:
        assert (await read_user_settings())["success"] is False
    profile.assert_not_awaited()


async def test_first_model_change_saves_a_complete_pair(fake_store: FakeStore) -> None:
    await patch_personal_settings("alice", {"default_model": "openai:gpt-6-sol"})
    profile = fake_store.values(["profiles"])["alice"]
    assert profile["default_model"] == "openai:gpt-6-sol"
    assert isinstance(profile["reasoning_effort"], str)


def test_tool_schema_accepts_partial_patch_without_identity_arguments() -> None:
    tool = StructuredTool.from_function(coroutine=save_user_settings)
    schema = tool.args_schema
    assert schema is not None
    parsed = schema.model_validate({"settings": {"draft_prs": False, "branch_prefix": None}})
    assert parsed.model_dump() == {"settings": {"draft_prs": False, "branch_prefix": None}}
