from unittest.mock import patch

import pytest
from pydantic import ValidationError

from openswe.skill_store.store import (
    SkillCreate,
    create_skill,
    get_skill,
    skills_backend,
)
from openswe.tools.organization_skills import save_organization_skill
from openswe.users import User


async def test_skill_validation_and_persistence(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "octocat")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    await User.sign_in("github", "1", login="octocat")

    with pytest.raises(ValidationError):
        SkillCreate(name="Invalid Name", description="Useful")

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
    assert await get_skill("Octocat", "review-feedback") == record
    assert await get_skill("octocat", "other") is None
    (file,) = await skills_backend("octocat").adownload_files(["/review-feedback/SKILL.md"])
    assert file.content == record["content"].encode()


async def test_save_organization_skill_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with patch(
        "openswe.run_config.get_config",
        return_value={"configurable": {"github_login": "someone-else"}},
    ):
        result = await save_organization_skill("deslop", "Minimize diffs")

    assert result["ok"] is False
