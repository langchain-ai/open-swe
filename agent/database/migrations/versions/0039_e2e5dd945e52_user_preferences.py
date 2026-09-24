"""User preferences"""

from alembic import op

revision = "e2e5dd945e52"
down_revision = "0a80973775d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN preferences jsonb NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    raise NotImplementedError
