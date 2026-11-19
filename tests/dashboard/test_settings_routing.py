import asyncio
from collections.abc import Callable

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.dashboard.settings import (
    REVIEW_SCOUT_FALLBACK_MODEL,
    Settings,
    SettingsUpdate,
    get_settings,
    upsert_instance_settings,
)
from openswe.dashboard.settings_records import INSTANCE_SETTINGS
from openswe.tools.manage_model_defaults import manage_model_defaults
from openswe.utils.json_types import JsonObject


def test_review_scout_ignores_balanced_routing_tier() -> None:
    settings = Settings(
        {
            "default_agent_routing_balanced_model": "anthropic:claude-opus-5-5",
            "default_agent_routing_balanced_reasoning_effort": "high",
        }
    )
    assert settings.review_scout_model == REVIEW_SCOUT_FALLBACK_MODEL


def test_review_scout_uses_its_own_setting_and_falls_back_when_invalid() -> None:
    configured = Settings(
        {
            "default_review_scout_model": "anthropic:claude-opus-5-5",
            "default_review_scout_reasoning_effort": "medium",
        }
    )
    assert configured.review_scout_model == ("anthropic:claude-opus-5-5", "medium")

    invalid = Settings(
        {
            "default_review_scout_model": "bogus:model",
            "default_review_scout_reasoning_effort": "low",
        }
    )
    assert invalid.review_scout_model == REVIEW_SCOUT_FALLBACK_MODEL


@pytest.mark.usefixtures("registry_db")
async def test_manage_model_defaults_sets_and_resets_review_scout(
    grant_tool_access: Callable[..., None],
) -> None:
    grant_tool_access(admin=True, admin_surface=True)
    await upsert_instance_settings(SettingsUpdate(org_guidelines="Keep me"))

    updated = await manage_model_defaults(
        "set", "review_scout", "anthropic:claude-opus-5-5", "medium"
    )
    assert updated["effective"]["review_scout"] == {
        "model": "anthropic:claude-opus-5-5",
        "reasoning_effort": "medium",
    }
    assert (await get_settings())["org_guidelines"] == "Keep me"

    with pytest.raises(ValidationError):
        await manage_model_defaults("set", "review_scout", "bogus:model", "low")

    await manage_model_defaults("set", "review_scout")
    assert (await get_settings()).review_scout_model == REVIEW_SCOUT_FALLBACK_MODEL


@pytest.mark.usefixtures("registry_db")
async def test_manage_model_defaults_enforces_sole_writer_and_admin_access(
    grant_tool_access: Callable[..., None],
) -> None:
    grant_tool_access(admin=True, sole=True)
    assert await manage_model_defaults(
        "set", "review_scout", "anthropic:claude-opus-5-5", "medium"
    ) == {"ok": True, "scope": "instance"}
    assert "not available in this thread" in str((await manage_model_defaults("read"))["error"])


@pytest.mark.usefixtures("registry_db")
async def test_manage_model_defaults_preserves_concurrent_role_updates(
    grant_tool_access: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grant_tool_access(admin=True, admin_surface=True)
    real_lock = INSTANCE_SETTINGS.lock
    first_locked = asyncio.Event()
    second_entered = asyncio.Event()
    calls = 0

    async def overlapping_lock(key: str = "", *, session: AsyncSession) -> JsonObject:
        nonlocal calls
        calls += 1
        call_num = calls
        if call_num == 1:
            row = await real_lock(key, session=session)
            first_locked.set()
            await second_entered.wait()
            return row
        await first_locked.wait()
        second_entered.set()
        return await real_lock(key, session=session)

    monkeypatch.setattr(INSTANCE_SETTINGS, "lock", overlapping_lock)
    async with asyncio.timeout(10):
        await asyncio.gather(
            manage_model_defaults("set", "reviewer", "anthropic:claude-opus-5-5", "high"),
            manage_model_defaults("set", "review_scout", "anthropic:claude-opus-5-5", "medium"),
        )

    effective = await get_settings()
    assert effective.default_model("reviewer") == ("anthropic:claude-opus-5-5", "high")
    assert effective.review_scout_model == ("anthropic:claude-opus-5-5", "medium")
