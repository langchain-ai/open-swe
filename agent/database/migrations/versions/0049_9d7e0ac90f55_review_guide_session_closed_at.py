"""When a review guide session was closed, so pull request updates stop waking it."""

from alembic import op

revision = "9d7e0ac90f55"
down_revision = "50a8631f0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE review_guide_session ADD COLUMN closed_at timestamptz")


def downgrade() -> None:
    raise NotImplementedError
