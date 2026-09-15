"""Schema regressions for the workspace, workspace_repository, and workspace_slack_channel tables."""

from uuid import UUID, uuid7

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from agent.database import postgres

pytestmark = pytest.mark.usefixtures("registry_db")

_TABLE_NAMES = ("workspace", "workspace_repository", "workspace_slack_channel")


async def _insert_repository(conn: AsyncConnection, repository_id: UUID) -> None:
    await conn.execute(
        text("INSERT INTO repository (id, key, full_name) VALUES (:id, :key, :full_name)"),
        {
            "id": repository_id,
            "key": f"owner/{repository_id}",
            "full_name": f"owner/{repository_id}",
        },
    )


async def _insert_workspace(conn: AsyncConnection, workspace_id: UUID, slug: str) -> None:
    await conn.execute(
        text("INSERT INTO workspace (id, slug, name) VALUES (:id, :slug, :name)"),
        {"id": workspace_id, "slug": slug, "name": slug},
    )


async def test_workspace_tables_exist() -> None:
    async with postgres.connection() as conn:
        found = await conn.scalars(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = ANY(:names)"
            ),
            {"schema": postgres.SCHEMA, "names": list(_TABLE_NAMES)},
        )
    assert set(found) == set(_TABLE_NAMES)


async def test_a_repository_can_belong_to_only_one_workspace() -> None:
    repository_id = uuid7()
    workspace_a = uuid7()
    workspace_b = uuid7()
    async with postgres.transaction() as conn:
        await _insert_repository(conn, repository_id)
        await _insert_workspace(conn, workspace_a, "team-a")
        await _insert_workspace(conn, workspace_b, "team-b")
        await conn.execute(
            text(
                "INSERT INTO workspace_repository (repository_id, workspace_id) "
                "VALUES (:repository_id, :workspace_id)"
            ),
            {"repository_id": repository_id, "workspace_id": workspace_a},
        )

    with pytest.raises(IntegrityError):
        async with postgres.transaction() as conn:
            await conn.execute(
                text(
                    "INSERT INTO workspace_repository (repository_id, workspace_id) "
                    "VALUES (:repository_id, :workspace_id)"
                ),
                {"repository_id": repository_id, "workspace_id": workspace_b},
            )


@pytest.mark.parametrize(
    ("column", "value"),
    [("refresh_status", "'wat'"), ("refresh_kind", "'sideways'"), ("snapshot_status", "'wat'")],
)
async def test_a_status_the_record_model_rejects_cannot_be_stored(column: str, value: str) -> None:
    """The Literal types in the store and the columns must not drift apart."""
    workspace_id = uuid7()
    with pytest.raises(IntegrityError):
        async with postgres.transaction() as conn:
            await _insert_workspace(conn, workspace_id, "team-a")
            await conn.execute(
                text(f"UPDATE workspace SET {column} = {value} WHERE id = :id"),
                {"id": workspace_id},
            )


async def test_a_refresh_kind_is_optional_until_a_refresh_runs() -> None:
    workspace_id = uuid7()
    async with postgres.transaction() as conn:
        await _insert_workspace(conn, workspace_id, "team-a")
        stored = await conn.scalar(
            text("SELECT refresh_kind FROM workspace WHERE id = :id"), {"id": workspace_id}
        )
    assert stored is None


async def test_a_slack_channel_can_belong_to_only_one_workspace() -> None:
    channel_id = "C0123456789"
    workspace_a = uuid7()
    workspace_b = uuid7()
    async with postgres.transaction() as conn:
        await _insert_workspace(conn, workspace_a, "team-a")
        await _insert_workspace(conn, workspace_b, "team-b")
        await conn.execute(
            text(
                "INSERT INTO workspace_slack_channel (channel_id, workspace_id) "
                "VALUES (:channel_id, :workspace_id)"
            ),
            {"channel_id": channel_id, "workspace_id": workspace_a},
        )

    with pytest.raises(IntegrityError):
        async with postgres.transaction() as conn:
            await conn.execute(
                text(
                    "INSERT INTO workspace_slack_channel (channel_id, workspace_id) "
                    "VALUES (:channel_id, :workspace_id)"
                ),
                {"channel_id": channel_id, "workspace_id": workspace_b},
            )
