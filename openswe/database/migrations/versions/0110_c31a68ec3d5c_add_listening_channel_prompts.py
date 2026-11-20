"""Add listening channel prompts"""

from alembic import op

revision = "c31a68ec3d5c"
down_revision = "d9d2e51c4a9e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE integration_slack_channel ADD COLUMN prompt text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE integration_slack_channel ALTER COLUMN mode SET DEFAULT 'kitchen'")
    op.execute("DELETE FROM integration_slack_channel")


def downgrade() -> None:
    raise NotImplementedError
