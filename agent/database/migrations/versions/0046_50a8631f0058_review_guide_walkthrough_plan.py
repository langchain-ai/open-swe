"""The review guide's walkthrough plan: its chunks, Other, and how far the reader got."""

from alembic import op

revision = "50a8631f0058"
down_revision = "061a2f07b128"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE review_guide_session ADD COLUMN plan jsonb")


def downgrade() -> None:
    raise NotImplementedError
