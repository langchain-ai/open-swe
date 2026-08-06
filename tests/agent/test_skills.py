from __future__ import annotations

from unittest.mock import ANY, AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.dashboard.skills import SkillCreate, create_skill, list_skills
from agent.tools.user_skills import save_user_skill


async def test_skill_validation_and_persistence() -> None:
    put_item = AsyncMock()
    client = AsyncMock()
    client.store.put_item = put_item

    with pytest.raises(ValidationError):
        SkillCreate(name="Invalid Name", description="Useful")

    with (
        patch("agent.dashboard.skills._client", return_value=client),
        patch("agent.dashboard.skills.get_skill", new_callable=AsyncMock, return_value=None),
    ):
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

    with patch("agent.dashboard.skills._client", return_value=client):
        page = await list_skills("octocat", limit=1, offset=3)

    assert page == {"items": [{"name": "first"}], "next_offset": 4}
    client.store.search_items.assert_awaited_once_with(
        ["user_skills", "octocat"], limit=2, offset=3
    )
