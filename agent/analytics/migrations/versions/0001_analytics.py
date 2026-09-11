from agent.analytics.migrations.operations import execute_script

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade(migration_scripts: dict[str, str]) -> None:
    execute_script(migration_scripts[revision])


def downgrade() -> None:
    raise NotImplementedError
