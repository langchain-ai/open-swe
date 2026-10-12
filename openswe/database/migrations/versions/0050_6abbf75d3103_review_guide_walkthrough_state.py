"""The review guide keeps where the walkthrough stands, not an upfront plan."""

from alembic import op

revision = "6abbf75d3103"
down_revision = "9d7e0ac90f55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE review_guide_session SET plan = NULL")
    op.execute("ALTER TABLE review_guide_session RENAME COLUMN plan TO walkthrough")


def downgrade() -> None:
    raise NotImplementedError
