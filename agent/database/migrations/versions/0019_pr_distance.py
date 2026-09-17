"""Store opening revisions and merged pull request distance."""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE pull_request ADD COLUMN opening_base_sha text NOT NULL DEFAULT '', "
        "ADD COLUMN opening_head_sha text NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE pr_projection ADD COLUMN distance_basis_points integer "
        "CHECK (distance_basis_points BETWEEN 0 AND 10000)"
    )


def downgrade() -> None:
    raise NotImplementedError
