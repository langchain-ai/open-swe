"""Whether a review guide walks a reviewer or the pull request's own author."""

from alembic import op

revision = "061a2f07b128"
down_revision = "f5e9fd030c0f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE review_guide_session "
        "ADD COLUMN mode text NOT NULL DEFAULT 'reviewer' "
        "CHECK (mode IN ('reviewer', 'author'))"
    )


def downgrade() -> None:
    raise NotImplementedError
