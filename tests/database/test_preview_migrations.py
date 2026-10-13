from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import text

from openswe.database import postgres


def migration_tree(path: Path) -> ScriptDirectory:
    versions = path / "versions"
    versions.mkdir()
    for revision, parents, statement in (
        ("parent_a", None, "CREATE TABLE preview_history_probe (id integer)"),
        ("parent_b", None, "CREATE TABLE preview_merge_probe (id integer)"),
        ("orphan", ("parent_a", "parent_b"), "INSERT INTO preview_history_probe VALUES (1)"),
    ):
        (versions / f"{revision}.py").write_text(
            f"from alembic import op\nrevision = {revision!r}\ndown_revision = {parents!r}\n"
            f"def upgrade():\n    op.execute({statement!r})\n"
        )
    return ScriptDirectory(str(path))


@pytest.mark.parametrize(
    "bootstrap,symbolic_dependency", [(False, False), (False, True), (True, False)]
)
async def test_preview_restores_dropped_merge_and_runs_unapplied_migration(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    symbolic_dependency: bool,
    bootstrap: bool,
) -> None:
    monkeypatch.setenv("OPENSWE_ENV", "staging" if bootstrap else "preview")
    migrations = migration_tree(tmp_path)
    if symbolic_dependency:
        with (tmp_path / "versions" / "parent_b.py").open("a") as file:
            file.write("branch_labels = ('other_branch',)\n")
        orphan = tmp_path / "versions" / "orphan.py"
        orphan.write_text(
            orphan.read_text().replace(
                "down_revision = ('parent_a', 'parent_b')",
                "down_revision = 'parent_a'\ndepends_on = 'other_branch'",
            )
        )
        migrations = ScriptDirectory(str(tmp_path))
    if bootstrap:
        for path in (tmp_path / "versions").glob("*.py"):
            path.write_text(
                path.read_text()
                .replace("parent_a", "6f06bbadfc02")
                .replace("parent_b", "a823229a905f")
                .replace("'orphan'", "'7d34d8e5b6a3'")
            )
        migrations = ScriptDirectory(str(tmp_path))
    async with postgres.transaction() as conn:
        await conn.execute(text("CREATE SCHEMA preview_history_test"))
        await conn.run_sync(postgres.upgrade, migrations, "preview_history_test")
    monkeypatch.setenv("OPENSWE_ENV", "preview")
    (tmp_path / "versions" / "orphan.py").unlink()
    (tmp_path / "versions" / "new_head.py").write_text(
        "from alembic import op\nrevision = 'new_head'\ndown_revision = ('parent_a', 'parent_b')\n"
        "def upgrade():\n    op.execute('INSERT INTO preview_history_probe VALUES (2)')\n"
    )
    if bootstrap:
        path = tmp_path / "versions" / "new_head.py"
        path.write_text(
            path.read_text().replace("parent_a", "6f06bbadfc02").replace("parent_b", "a823229a905f")
        )
    async with postgres.transaction() as conn:
        await conn.run_sync(
            postgres.upgrade, ScriptDirectory(str(tmp_path)), "preview_history_test"
        )
        await conn.run_sync(
            postgres.upgrade, ScriptDirectory(str(tmp_path)), "preview_history_test"
        )
        assert list(
            (
                await conn.execute(
                    text("SELECT version_num FROM preview_history_test.alembic_version")
                )
            ).scalars()
        ) == ["new_head"]
        assert list(
            (await conn.execute(text("SELECT id FROM preview_history_probe ORDER BY id"))).scalars()
        ) == [1, 2]


@pytest.mark.parametrize("environment", ["prod", "staging"])
async def test_non_preview_keeps_unknown_revision_failure(
    registry_db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environment: str
) -> None:
    monkeypatch.setenv("OPENSWE_ENV", environment)
    migrations = migration_tree(tmp_path)
    async with postgres.transaction() as conn:
        await conn.execute(text("CREATE SCHEMA preview_history_test"))
        await conn.run_sync(postgres.upgrade, migrations, "preview_history_test")
        assert (
            await conn.scalar(
                text("SELECT to_regclass('preview_history_test.preview_migration_history')")
            )
            is None
        )
    (tmp_path / "versions" / "orphan.py").unlink()
    async with postgres.transaction() as conn:
        with pytest.raises(CommandError, match="orphan"):
            await conn.run_sync(
                postgres.upgrade, ScriptDirectory(str(tmp_path)), "preview_history_test"
            )


@pytest.mark.parametrize("orphan", ["unrecorded", "7d34d8e5b6a3"])
async def test_preview_unrecoverable_orphan_fails_without_restamping(
    registry_db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, orphan: str
) -> None:
    monkeypatch.setenv("OPENSWE_ENV", "preview")
    async with postgres.transaction() as conn:
        await conn.execute(text("CREATE SCHEMA preview_history_test"))
        await conn.execute(
            text("CREATE TABLE preview_history_test.alembic_version (version_num text PRIMARY KEY)")
        )
        await conn.execute(
            text("INSERT INTO preview_history_test.alembic_version VALUES (:orphan)"),
            {"orphan": orphan},
        )
        with pytest.raises(RuntimeError, match="verified parent revisions"):
            await conn.run_sync(postgres.upgrade, migration_tree(tmp_path), "preview_history_test")
        assert (
            await conn.scalar(text("SELECT version_num FROM preview_history_test.alembic_version"))
            == orphan
        )
