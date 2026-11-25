from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from deepagents.backends import FilesystemBackend
from fastapi import HTTPException

from openswe.skill_store.backfill import SkillBackfill
from openswe.skill_store.store import (
    HubSkills,
    SkillCreate,
    SkillUpdate,
    create_skill,
    list_skills,
    update_skill,
)
from openswe.tools.organization_skills import save_organization_skill
from openswe.users import User


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("ROBIN_SKILLS_HUB_PREFIX", raising=False)
    monkeypatch.setenv("OPENSWE_ENV", "production")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", str(UUID(int=1)))
    monkeypatch.setattr("openswe.skill_store.store.Client", MagicMock())

    def backend(repo: str, **kwargs: object) -> FilesystemBackend:
        root = tmp_path / repo.removeprefix("-/")
        root.mkdir(exist_ok=True)
        return FilesystemBackend(root_dir=str(root), virtual_mode=True)

    monkeypatch.setattr("deepagents.backends.context_hub.ContextHubBackend", backend)
    return tmp_path


async def test_skill_crud_is_scoped_to_its_owner_and_preserves_support_files(
    hub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, other = (MagicMock(spec=User, id=UUID(int=value)) for value in (2, 3))
    body = SkillCreate(name="review", description="Review changes", instructions="Read the diff")
    saved = await create_skill("owner", body, user=owner)
    broken = hub / f"robin-skills-user-{owner.id}" / "made-by-hand" / "SKILL.md"
    broken.parent.mkdir()
    broken.write_text("No frontmatter")
    assert (await list_skills("owner", limit=100, offset=0, user=owner))["items"] == [saved]
    assert (await list_skills("other", limit=100, offset=0, user=other))["items"] == []
    support = hub / f"robin-skills-user-{owner.id}" / "review" / "reference.md"
    support.write_text("Keep this")
    await update_skill("owner", "review", SkillUpdate(description="Updated"), user=owner)
    async with HubSkills.open(owner.id) as skills:
        assert (await skills.get("review"))["description"] == "Updated"
        await skills.delete(" review ")
        assert await skills.get("review") is None
    assert support.read_text() == "Keep this"
    async with HubSkills.open() as skills:
        await skills.save(body, create=True)
    assert (hub / "robin-skills" / "review" / "SKILL.md").is_file()
    # Staging shares production's workspace but never its skills.
    monkeypatch.setenv("OPENSWE_ENV", "staging")
    async with HubSkills.open() as skills:
        with pytest.raises(HTTPException) as caught:
            await skills.delete("review")
    assert caught.value.status_code == 404
    assert (hub / "robin-skills" / "review" / "SKILL.md").is_file()


async def test_a_skill_saved_with_windows_line_endings_can_be_edited() -> None:
    from deepagents.backends.context_hub import ContextHubBackend
    from langsmith import Client
    from langsmith.schemas import AgentContext, FileEntry

    client = MagicMock(spec=Client)
    client.pull_agent.return_value = AgentContext(
        commit_id=UUID(int=1),
        commit_hash="1" * 64,
        files={
            "review/SKILL.md": FileEntry(
                content="---\r\nname: review\r\ndescription: Review\r\n---\r\n\r\nRead the diff\r\n"
            )
        },
    )
    client.push_agent.return_value = f"https://smith.invalid/context/robin-skills/{'2' * 64}"
    skills = HubSkills(ContextHubBackend("-/robin-skills", client=client), None)
    await skills.save(
        SkillCreate(name="review", description="Updated", instructions="Read the diff"),
        create=False,
    )
    [pushed] = client.push_agent.call_args.kwargs["files"].values()
    assert "description: Updated\n" in pushed.content


@pytest.mark.usefixtures("registry_db")
async def test_save_organization_skill_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with patch(
        "openswe.run_config.get_config",
        return_value={"configurable": {"github_login": "someone-else"}},
    ):
        assert (await save_organization_skill("deslop", "Minimize diffs"))["ok"] is False


async def test_backfill_is_dry_run_and_idempotent_and_refuses_differing_files(
    hub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An emoji, U+0085 and Windows line endings must survive the copy for a rerun to see it
    # as identical.
    row = {
        "user_id": None,
        "name": "review",
        "value": {
            "description": "Review 🔍\x85then approve",
            "instructions": "Read diff\r\nThen review\r\n",
            "content": "DO NOT COPY",
        },
    }
    dry_run, apply = SkillBackfill(), SkillBackfill(apply=True)
    await dry_run.migrate(row)
    path = hub / "robin-skills" / "review" / "SKILL.md"
    assert not path.exists()
    await apply.migrate(row)
    before = path.read_text()
    await apply.migrate(row)
    assert path.read_text() == before and "Read diff" in before and "DO NOT COPY" not in before
    row["value"] = {"description": "Changed"}
    with pytest.raises(HTTPException) as caught:
        await apply.migrate(row)
    assert caught.value.status_code == 409 and path.read_text() == before
    # Without a named environment the copy would land in another environment's repository.
    monkeypatch.delenv("OPENSWE_ENV")
    with pytest.raises(SystemExit):
        SkillBackfill.target()


@pytest.mark.parametrize(
    ("scope", "allowed"),
    [
        ("owner", True),
        ("dm", True),
        ("public", False),
        ("sole-writer", False),
        ("channel", False),
        ("other-actor", False),
        ("wrong-user-id", False),
        ("automation", False),
        ("reviewer", False),
        ("system-owner", False),
        ("unlinked", True),
    ],
)
async def test_personal_skills_require_saved_private_owner_every_run(
    monkeypatch: pytest.MonkeyPatch, scope: str, allowed: bool
) -> None:
    from openswe.agent_api.run_kinds import RunKind
    from openswe.skill_store.run_skills import RunSkillStores
    from openswe.utils.json_types import JsonObject

    user = MagicMock(spec=User, id=UUID(int=2))
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=user))
    monkeypatch.setattr(User, "get", AsyncMock(return_value=user))
    config: JsonObject = {
        "thread_id": "thread-private",
        "github_login": "octocat",
        "user_id": str(user.id),
        "source": "dashboard",
    }
    metadata: JsonObject = {"owner_login": "octocat", "visibility": "private"}
    if scope in {"public", "sole-writer"}:
        metadata["visibility"] = "public"
        metadata["sole_writer_id"] = str(user.id)
    elif scope == "other-actor":
        config["github_login"] = "other"
    elif scope == "wrong-user-id":
        config["user_id"] = str(UUID(int=3))
    elif scope == "automation":
        config["schedule_id"] = "schedule"
    elif scope == "system-owner":
        metadata["owner_type"] = "system"
    elif scope in {"dm", "channel"}:
        metadata["source_context"] = {
            "slack_thread": {"channel_id": "D1" if scope == "dm" else "C1", "thread_ts": "1.0"}
        }
    elif scope == "unlinked":
        config["github_linked"] = False
        metadata["owner_login"] = f"user:{user.id}"
    monkeypatch.setattr(
        "openswe.skill_store.run_skills.read_thread", AsyncMock(return_value={"metadata": metadata})
    )
    stores = await RunSkillStores.for_run({"configurable": config})
    if scope == "reviewer":
        stores = await RunKind().skills({"configurable": config})
        allowed = False
    listed = await stores.listed()
    assert listed.organization_repo == HubSkills.repo()
    assert listed.personal_repo == (HubSkills.repo(user.id) if allowed else None)
    if allowed:
        metadata["visibility"] = "public"
        resumed = await RunSkillStores.for_run({"configurable": config})
        assert (await resumed.listed()).personal_repo is None
