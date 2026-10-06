from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from agent.skill_store.store import (
    SkillCreate,
    create_skill,
)
from agent.tools.organization_skills import save_organization_skill


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


async def test_save_organization_skill_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with patch(
        "agent.run_config.get_config",
        return_value={"configurable": {"github_login": "someone-else"}},
    ):
        result = await save_organization_skill("deslop", "Minimize diffs")

    assert result["ok"] is False
