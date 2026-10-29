"""Add frontend interaction journeys"""

from alembic import op

revision = "393bdf1b96e5"
down_revision = "311081ca2899"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE frontend_review_run ADD COLUMN journeys jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(journeys) = 'array')"
    )


def downgrade() -> None:
    raise NotImplementedError
