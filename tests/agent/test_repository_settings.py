import asyncio
from collections.abc import Callable
from importlib import import_module
from unittest.mock import AsyncMock, patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import delete, inspect, text, update
from sqlalchemy.engine import Connection

from openswe.database import postgres
from openswe.github.repositories import Repository
from openswe.review.approvals import ApprovalPolicy
from openswe.review.enabled_repos import list_enabled_review_repos, set_review_repo_enabled
from openswe.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate
from openswe.tools.settings import patch_settings, read_settings


@pytest.fixture(autouse=True)
def requester(grant_tool_access: Callable[..., None]):
    grant_tool_access(admin=True, admin_surface=True)
    with (
        patch(
            "openswe.run_config.get_config",
            return_value={"configurable": {"github_login": "admin"}},
        ),
        patch("openswe.tools.settings.require_repo_access_for_user", AsyncMock(return_value="t")),
        patch(
            "openswe.tools.settings.ApprovalPolicy.load",
            AsyncMock(return_value=ApprovalPolicy(approvals_md="private criteria")),
        ),
    ):
        yield


async def test_toggle_keeps_other_repositories_and_settings(registry_db: None) -> None:
    await set_review_repo_enabled("o/other", True)
    await REVIEW_STYLES.update_prompts(
        "o/r", ReviewStylePromptUpdate(approval_mode="off", custom_prompt="private prompt")
    )
    before = await read_settings("repository_reviews", repository="O/R")
    assert before["values"] == {"enabled": False}
    assert before["repository"] == "o/r"
    await patch_settings(
        "repository_reviews", {"enabled": True}, str(before["version"]), repository="O/R"
    )
    assert await list_enabled_review_repos() == ["o/other", "o/r"]
    approval = await read_settings("repository_approval", repository="o/r")
    assert approval["values"] == {"approval_mode": "off"}
    assert approval["approval_criteria_on_default_branch"] is True
    assert "private prompt" not in str(approval) and "private criteria" not in str(approval)
    await patch_settings(
        "repository_approval", {"approval_mode": None}, str(approval["version"]), repository="o/r"
    )
    style = await REVIEW_STYLES.get("o/r")
    assert (
        style is not None
        and style.custom_prompt == "private prompt"
        and style.approval_mode is None
    )
    assert (await read_settings("repository_approval", repository="o/r"))[
        "effective_mode"
    ] == "dry_run"
    assert await list_enabled_review_repos() == ["o/other", "o/r"]


@pytest.mark.parametrize(
    "resource,change",
    [
        ("repository_reviews", {"enabled": True}),
        ("repository_approval", {"approval_mode": "approve"}),
    ],
)
async def test_concurrent_create_update_and_delete_refuse_stale_versions(
    registry_db: None, resource, change
) -> None:
    missing = await read_settings(resource, repository="o/r")
    assert missing["version"] == "missing"
    results = await asyncio.gather(
        *(patch_settings(resource, change, "missing", repository="o/r") for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    current = await read_settings(resource, repository="o/r")
    results = await asyncio.gather(
        *(
            patch_settings(resource, change, str(current["version"]), repository="o/r")
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    current = await read_settings(resource, repository="o/r")
    if resource == "repository_approval":
        await REVIEW_STYLES.delete("o/r")
    else:
        async with postgres.session() as session:
            await session.execute(delete(Repository).where(Repository.key == "o/r"))
    with pytest.raises(ValueError, match="Settings changed"):
        await patch_settings(resource, change, str(current["version"]), repository="o/r")
    assert (await read_settings(resource, repository="o/r"))["version"] == "missing"


async def test_dashboard_and_legacy_writers_invalidate_versions(registry_db: None) -> None:
    await set_review_repo_enabled("o/r", True)
    before = await Repository.get("o/r")
    assert before is not None and before.review_settings_version is not None
    assert before.review_settings_version.version == 7
    await set_review_repo_enabled("o/r", False)
    with pytest.raises(ValueError, match="Settings changed"):
        await patch_settings(
            "repository_reviews", {"enabled": True}, before.review_version, repository="o/r"
        )
    current = await Repository.get("o/r")
    assert current is not None
    async with postgres.session() as session:
        await session.execute(
            update(Repository).where(Repository.key == "o/r").values(review_enabled=False)
        )
    with pytest.raises(ValueError, match="Settings changed"):
        await patch_settings(
            "repository_reviews", {"enabled": True}, current.review_version, repository="o/r"
        )
    await REVIEW_STYLES.update_prompts("o/r", ReviewStylePromptUpdate(approval_mode="off"))
    approval = await read_settings("repository_approval", repository="o/r")
    await REVIEW_STYLES.update_prompts(
        "o/r", ReviewStylePromptUpdate(custom_prompt="changed in dashboard")
    )
    with pytest.raises(ValueError, match="Settings changed"):
        await patch_settings(
            "repository_approval",
            {"approval_mode": "approve"},
            str(approval["version"]),
            repository="o/r",
        )
    style = await REVIEW_STYLES.get("o/r")
    assert (
        style is not None
        and style.approval_mode == "off"
        and style.custom_prompt == "changed in dashboard"
    )


@pytest.mark.parametrize(
    "resource,change",
    [
        ("repository_reviews", {"enabled": True}),
        ("repository_approval", {"approval_mode": "approve"}),
    ],
)
async def test_repository_access_denial_prevents_reads_and_mutation(
    registry_db: None, resource, change
) -> None:
    with patch(
        "openswe.tools.settings.require_repo_access_for_user",
        AsyncMock(side_effect=HTTPException(403, "No access")),
    ):
        with pytest.raises(HTTPException):
            await read_settings(resource, repository="private/r")
        with pytest.raises(HTTPException):
            await patch_settings(resource, change, "missing", repository="private/r")
    assert await Repository.get("private/r") is None


@pytest.mark.parametrize("admin,surface", [(False, True), (True, False)])
async def test_unauthorized_calls_cannot_read_or_write(
    registry_db: None, grant_tool_access: Callable[..., None], admin: bool, surface: bool
) -> None:
    grant_tool_access(admin=admin, admin_surface=surface)
    assert "not available in this thread" in str(
        await read_settings("repository_reviews", repository="o/r")
    )
    assert "not available in this thread" in str(
        await patch_settings("repository_reviews", {"enabled": True}, "missing", repository="o/r")
    )
    assert await Repository.get("o/r") is None


@pytest.mark.parametrize(
    "resource,change",
    [
        ("repository_reviews", {"enabled": True}),
        ("repository_approval", {"approval_mode": "approve"}),
    ],
)
async def test_sole_writer_gets_only_acknowledgement(
    registry_db: None, grant_tool_access: Callable[..., None], resource, change
) -> None:
    before = await read_settings(resource, repository="o/r")
    grant_tool_access(admin=True, sole=True)
    with patch(
        "openswe.tools.settings.ApprovalPolicy.load",
        AsyncMock(side_effect=AssertionError("Shared mutation must not fetch policy")),
    ):
        assert await patch_settings(resource, change, str(before["version"]), repository="o/r") == {
            "ok": True,
            "resource": resource,
            "scope": "repository",
            "repository": "o/r",
        }
    assert "not available in this thread" in str(await read_settings(resource, repository="o/r"))
    assert "not available in this thread" in str(await read_settings(resource, schema_only=True))


@pytest.mark.parametrize(
    "resource,change",
    [
        ("repository_reviews", {"enabled": None}),
        ("repository_reviews", {"enabled": "yes"}),
        ("repository_approval", {"custom_prompt": "no"}),
        ("repository_approval", {"approval_mode": "yes"}),
        ("repository_reviews", {}),
    ],
)
async def test_invalid_patches_do_not_write(registry_db: None, resource, change) -> None:
    with pytest.raises(ValueError):
        await patch_settings(resource, change, "missing", repository="o/r")
    assert await Repository.get("o/r") is None


async def test_resource_scopes_cannot_mix(registry_db: None) -> None:
    with pytest.raises(ValueError):
        await read_settings("feature_flags", repository="o/r")
    with pytest.raises(ValueError):
        await read_settings("repository_approval")
    assert await Repository.get("o/r") is None


class TestReviewVersionMigration:
    @staticmethod
    def roundtrip(connection: Connection) -> None:
        migration = import_module(
            "openswe.database.migrations.versions.0109_c9501de19ea1_version_repository_review_settings"
        )
        context = MigrationContext.configure(
            connection, opts={"version_table_schema": postgres.SCHEMA}
        )
        with Operations.context(context):
            migration.downgrade()
            assert "review_settings_version" not in {
                column["name"]
                for column in inspect(connection).get_columns("repository", schema=postgres.SCHEMA)
            }
            assert (
                connection.scalar(
                    text("SELECT to_regprocedure(:name)"),
                    {"name": f"{postgres.SCHEMA}.version_repository_reviews()"},
                )
                is None
            )
            connection.execute(
                text("UPDATE repository SET review_enabled = true WHERE key = 'o/r'")
            )
            migration.upgrade()
            migration.upgrade()

    async def test_roundtrip_preserves_legacy_settings(self, registry_db: None) -> None:
        await set_review_repo_enabled("o/r", False)
        async with postgres.transaction() as connection:
            await connection.run_sync(self.roundtrip)
        before = await Repository.get("o/r")
        assert (
            before is not None
            and before.review_enabled is True
            and before.review_settings_version is None
        )
        await patch_settings(
            "repository_reviews", {"enabled": False}, before.review_version, repository="o/r"
        )
        current = await Repository.get("o/r")
        assert current is not None and current.review_enabled is False
        assert (
            current.review_settings_version is not None
            and current.review_settings_version.version == 7
        )


async def test_full_repository_style_and_instructions_use_existing_writers(
    registry_db: None,
) -> None:
    before = await read_settings("repository_style", repository="o/r")
    assert "custom_prompt" in before["schema"]["properties"]
    await patch_settings(
        "repository_style",
        {"custom_prompt": "Full review guidance", "approval_mode": "off"},
        str(before["version"]),
        repository="o/r",
    )
    assert (await read_settings("repository_style", repository="o/r"))["values"][
        "custom_prompt"
    ] == "Full review guidance"
    await patch_settings(
        "repository_instructions", {"instructions": "Repository guidance"}, repository="o/r"
    )
    instructions = await read_settings("repository_instructions", repository="o/r")
    assert instructions["values"]["instructions"] == "Repository guidance"
    with pytest.raises(ValueError):
        await patch_settings(
            "repository_instructions",
            {"instructions": "No stale token"},
            "missing",
            repository="o/r",
        )
