from unittest.mock import ANY, AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.settings.skills import (
    SkillCreate,
    create_organization_skill,
    create_skill,
    list_organization_skills,
    list_skills,
)
from agent.tools.organization_skills import save_organization_skill
from agent.tools.user_skills import save_user_skill


async def test_skill_validation_and_persistence() -> None:
    put_item = AsyncMock()
    client = AsyncMock()
    client.store.put_item = put_item

    with pytest.raises(ValidationError):
        SkillCreate(name="Invalid Name", description="Useful")

    client.store.get_item.return_value = None

    with patch("agent.store.store_client", return_value=client):
        record = await create_skill(
            "octocat",
            SkillCreate(
                name="review-feedback",
                description="Address PR review feedback",
                instructions="Check every open comment.",
            ),
        )

    assert record["content"] == (
        '---\nname: "review-feedback"\n'
        'description: "Address PR review feedback"\n---\n\n'
        "Check every open comment.\n"
    )
    put_item.assert_awaited_once_with(
        ["user_skills", "octocat"],
        "/review-feedback/SKILL.md",
        record,
    )


async def test_organization_skill_uses_singleton_namespace() -> None:
    client = AsyncMock()
    client.store.get_item.return_value = None
    client.store.search_items.return_value = {"items": []}

    with patch("agent.store.store_client", return_value=client):
        await create_organization_skill(
            SkillCreate(name="security-review", description="Apply organization security rules")
        )

    client.store.put_item.assert_awaited_once()
    assert client.store.put_item.await_args.args[:2] == (
        ["organization_skills"],
        "/security-review/SKILL.md",
    )


async def test_organization_skill_listing_uses_opaque_cursor() -> None:
    client = AsyncMock()
    client.store.search_items.return_value = {
        "items": [
            {"value": {"name": "second"}},
            {"value": {"name": "first"}},
        ]
    }

    with patch("agent.store.store_client", return_value=client):
        first_page = await list_organization_skills(limit=1, cursor=None)
        second_page = await list_organization_skills(limit=1, cursor=first_page["next_cursor"])
        for cursor in (
            "",
            "invalid",
            "☃",
            "eyJuYW1lIjogImZpcnN0In0!!!!",
            "eyJuYW1lIjogIiJ9",
            "eyJuYW1lIjogImZpcnN0IiwgImV4dHJhIjogdHJ1ZX0",
        ):
            with pytest.raises(Exception, match="invalid cursor"):
                await list_organization_skills(limit=1, cursor=cursor)

    assert first_page == {"items": [{"name": "first"}], "next_cursor": "eyJuYW1lIjogImZpcnN0In0"}
    assert second_page == {"items": [{"name": "second"}], "next_cursor": None}
    client.store.search_items.assert_awaited_with(
        ["organization_skills"], filter=None, limit=1001, offset=0
    )


async def test_save_user_skill_uses_triggering_user_namespace() -> None:
    create = AsyncMock(return_value={"name": "deslop"})
    with (
        patch(
            "agent.tools.user_skills.get_config",
            return_value={"configurable": {"github_login": "octocat"}},
        ),
        patch("agent.tools.user_skills.get_skill", new_callable=AsyncMock, return_value=None),
        patch("agent.tools.user_skills.create_skill", create),
    ):
        result = await save_user_skill("deslop", "Minimize diffs", "Remove bloat.")

    assert result == {"ok": True, "skill": {"name": "deslop"}}
    create.assert_awaited_once_with("octocat", ANY)


async def test_skill_listing_returns_next_offset() -> None:
    client = AsyncMock()
    client.store.search_items.return_value = {
        "items": [
            {"value": {"name": "first"}},
            {"value": {"name": "second"}},
        ]
    }

    with patch("agent.store.store_client", return_value=client):
        page = await list_skills("octocat", limit=1, offset=3)

    assert page == {"items": [{"name": "first"}], "next_offset": 4}
    client.store.search_items.assert_awaited_once_with(
        ["user_skills", "octocat"], filter=None, limit=2, offset=3
    )


async def test_save_organization_skill_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with patch(
        "agent.tools.admin_gate.get_config",
        return_value={"configurable": {"github_login": "someone-else"}},
    ):
        result = await save_organization_skill("deslop", "Minimize diffs")

    assert result["ok"] is False
