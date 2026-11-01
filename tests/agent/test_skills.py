from unittest.mock import patch

import pytest
from pydantic import ValidationError

from openswe.skill_store.backend import skills_backend
from openswe.skill_store.store import (
    SkillCreate,
    SkillImport,
    create_skill,
    get_skill,
    list_skills,
    update_skill,
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


async def test_desktop_import_preserves_resources_and_cloud_edits(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from openswe.skill_store.run_skills import RunSkillStores
    from openswe.skill_store.store import SkillUpdate

    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "octocat,other")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    await User.sign_in("github", "1", login="octocat")
    await User.sign_in("github", "2", login="other")
    content = "---\nname: review\ndescription: >-\n  Review code safely\nlicense: MIT\n---\nCheck references/checklist.md\n"
    bundle = SkillImport(files={"SKILL.md": content, "references/checklist.md": "Check auth"})
    await bundle.save("octocat", "review")
    bundle.files["references/checklist.md"] = "Check permissions"
    await bundle.save("octocat", "review")
    listed = await RunSkillStores("octocat").listed()
    files = {file.path: file.content for file in listed.files}
    assert files["/skills/review/SKILL.md"] == content
    assert files["/skills/review/references/checklist.md"] == "Check permissions"
    assert await get_skill("other", "review") is None
    page = await list_skills("octocat", limit=100, offset=0)
    assert "files" not in page["items"][0]
    await update_skill(
        "octocat", "review", SkillUpdate(description="Cloud edit", instructions="Keep me")
    )
    with pytest.raises(HTTPException) as conflict:
        await bundle.save("octocat", "review")
    assert conflict.value.status_code == 409
    saved = await get_skill("octocat", "review")
    assert saved is not None and saved["instructions"] == "Keep me"
    assert saved["files"]["references/checklist.md"] == "Check permissions"
    assert "license: MIT" in saved["content"]
    assert saved["files"]["SKILL.md"] == saved["content"]
    assert "source" not in saved


@pytest.mark.parametrize(
    "path", ["../secret", "/secret", "references/../../secret", "a\\\\b", ".env"]
)
def test_desktop_import_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        SkillImport(files={"SKILL.md": "content", path: "secret"})


async def test_save_organization_skill_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with patch(
        "openswe.run_config.get_config",
        return_value={"configurable": {"github_login": "someone-else"}},
    ):
        result = await save_organization_skill("deslop", "Minimize diffs")

    assert result["ok"] is False
