"""Expedited review excluded hunks"""

from alembic import op

revision = "42e9e3af43e3"
down_revision = ["6f06bbadfc02", "a823229a905f", "1b0248b89a95"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE human_review_request "
        "ADD COLUMN IF NOT EXISTS excluded_hunks jsonb NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    raise NotImplementedError
