from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from langsmith.schemas import AgentEntry, FileEntry

from agent.dashboard import skills
from agent.dashboard.skills import (
    SkillPayload,
    compose_skill_md,
    create_user_skill,
    delete_user_skill,
    list_user_skills,
    parse_skill_md,
    update_user_skill,
    validate_skill_name,
)


@pytest.fixture(autouse=True)
def _skills_repo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSWE_SKILLS_REPO", raising=False)


def test_validate_skill_name() -> None:
    assert validate_skill_name("my-skill") == "my-skill"
    for bad in ["", "-x", "x-", "a--b", "UPPER", "has space", "a" * 65, "sym!"]:
        with pytest.raises(ValueError):
            validate_skill_name(bad)


def test_compose_and_parse_round_trip() -> None:
    payload = SkillPayload(name="run-tests", description="How to run tests", body="# Steps\n1. go")
    content = compose_skill_md(payload)
    assert content.startswith("---\n")
    parsed = parse_skill_md("run-tests", content)
    assert parsed == {
        "name": "run-tests",
        "description": "How to run tests",
        "body": "# Steps\n1. go",
    }


def test_parse_skill_md_malformed_falls_back_to_dir_name() -> None:
    parsed = parse_skill_md("legacy", "no frontmatter here")
    assert parsed["name"] == "legacy"
    assert parsed["description"] == ""
    assert parsed["body"] == "no frontmatter here"


def test_payload_validation() -> None:
    with pytest.raises(ValueError):
        SkillPayload(name="ok", description="   ")
    with pytest.raises(ValueError):
        SkillPayload(name="Bad Name", description="d")


def test_repo_derived_from_login_only() -> None:
    # Repo is keyed by the passed login, never by request input.
    assert skills._repo_for("octocat") == "-/openswe-skills-octocat"


async def test_repo_disabled_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSWE_SKILLS_REPO", "")
    with pytest.raises(HTTPException) as exc:
        await list_user_skills("octocat")
    assert exc.value.status_code == 400


async def test_list_parses_skills_and_skips_linked_entries() -> None:
    files = {
        "run-tests/SKILL.md": FileEntry(
            type="file", content="---\nname: run-tests\ndescription: d\n---\nbody"
        ),
        "alpha/SKILL.md": FileEntry(type="file", content="---\nname: alpha\ndescription: a\n---\n"),
        "linked/SKILL.md": AgentEntry(type="agent", repo_handle="other/skill"),
        "run-tests/helper.py": FileEntry(type="file", content="print(1)"),
    }
    with patch.object(skills, "_pull_files", MagicMock(return_value=(files, "c1"))):
        out = await list_user_skills("octocat")
    names = [s["name"] for s in out]
    assert names == ["alpha", "run-tests"]  # sorted, linked + non-SKILL.md skipped


async def test_create_pushes_and_conflicts() -> None:
    push = MagicMock()
    with (
        patch.object(skills, "_pull_files", MagicMock(return_value=({}, None))),
        patch.object(skills, "_push", push),
    ):
        await create_user_skill(
            "octocat", SkillPayload(name="new-skill", description="d", body="b")
        )
    push.assert_called_once()
    repo, files, parent = push.call_args.args
    assert repo == "-/openswe-skills-octocat"
    assert "new-skill/SKILL.md" in files
    assert parent is None

    existing = {"new-skill/SKILL.md": FileEntry(type="file", content="x")}
    with (
        patch.object(skills, "_pull_files", MagicMock(return_value=(existing, "c1"))),
        patch.object(skills, "_push", MagicMock()),
        pytest.raises(HTTPException) as exc,
    ):
        await create_user_skill("octocat", SkillPayload(name="new-skill", description="d"))
    assert exc.value.status_code == 409


async def test_update_requires_existing_and_no_rename() -> None:
    existing = {"a/SKILL.md": FileEntry(type="file", content="x")}
    # rename attempt -> 400
    with (
        patch.object(skills, "_pull_files", MagicMock(return_value=(existing, "c1"))),
        patch.object(skills, "_push", MagicMock()),
        pytest.raises(HTTPException) as exc,
    ):
        await update_user_skill("octocat", "a", SkillPayload(name="b", description="d"))
    assert exc.value.status_code == 400

    # missing -> 404
    with (
        patch.object(skills, "_pull_files", MagicMock(return_value=({}, None))),
        patch.object(skills, "_push", MagicMock()),
        pytest.raises(HTTPException) as exc,
    ):
        await update_user_skill("octocat", "a", SkillPayload(name="a", description="d"))
    assert exc.value.status_code == 404

    push = MagicMock()
    with (
        patch.object(skills, "_pull_files", MagicMock(return_value=(existing, "c1"))),
        patch.object(skills, "_push", push),
    ):
        await update_user_skill("octocat", "a", SkillPayload(name="a", description="d", body="new"))
    push.assert_called_once()


async def test_delete_pushes_none_marker_and_404() -> None:
    existing = {"a/SKILL.md": FileEntry(type="file", content="x")}
    push = MagicMock()
    with (
        patch.object(skills, "_pull_files", MagicMock(return_value=(existing, "c1"))),
        patch.object(skills, "_push", push),
    ):
        await delete_user_skill("octocat", "a")
    repo, files, parent = push.call_args.args
    assert files == {"a/SKILL.md": None}
    assert parent == "c1"

    with (
        patch.object(skills, "_pull_files", MagicMock(return_value=({}, None))),
        patch.object(skills, "_push", MagicMock()),
        pytest.raises(HTTPException) as exc,
    ):
        await delete_user_skill("octocat", "missing")
    assert exc.value.status_code == 404
