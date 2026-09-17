"""Leaderboard name backfill safety checks."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from scripts import backfill_leaderboard_names as backfill


async def test_backfill_preserves_names_and_retention(analytics_db, monkeypatch):
    workspace, transaction = analytics_db
    records = [("linted", None, False), ("named", "Existing", False), ("expired", None, True)]
    async with transaction() as conn:
        for login, name, expired in records:
            await conn.execute(
                text("""
                    INSERT INTO identity_directory
                        (workspace_id, person_id, github_login, display_name, anonymize_after)
                    VALUES (:workspace, :person, :login, :name,
                        clock_timestamp() + (:days * interval '1 day'))
                """),
                {
                    "workspace": workspace,
                    "person": uuid4(),
                    "login": login,
                    "name": name,
                    "days": -1 if expired else 1,
                },
            )
    monkeypatch.setattr(backfill, "github_name", AsyncMock(return_value="Mike Merrill"))

    async def names():
        async with transaction() as conn:
            return list(
                await conn.execute(
                    text(
                        "SELECT github_login, display_name, anonymize_after FROM identity_directory ORDER BY github_login"
                    )
                )
            )

    original = await names()
    assert await backfill.backfill() == 1
    assert await names() == original
    assert await backfill.backfill(apply=True) == 1
    updated = await names()
    assert [(row.github_login, row.display_name) for row in updated] == [
        ("expired", None),
        ("linted", "Mike Merrill"),
        ("named", "Existing"),
    ]
    assert [row.anonymize_after for row in updated] == [row.anonymize_after for row in original]
    assert await backfill.backfill(apply=True) == 0


@pytest.mark.parametrize(
    "output,expected", [(b"Mike Merrill\n", "Mike Merrill"), (b"null\n", None), (b"  \n", None)]
)
async def test_profile_name(monkeypatch, output, expected):
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (output, b"")
    monkeypatch.setattr(backfill.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    assert await backfill.github_name("linted") == expected


async def test_profile_failure_stops_backfill(monkeypatch):
    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = (b"", b"rate limit exceeded")
    monkeypatch.setattr(backfill.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        await backfill.github_name("linted")
