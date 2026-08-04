from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.dashboard.skills import SkillCreate, create_skill


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
