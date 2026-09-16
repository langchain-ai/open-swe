"""Store the configured reasoning effort for agent runs."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE run_projection ADD COLUMN configured_effort text")


def downgrade() -> None:
    raise NotImplementedError
