"""Track reviewer run costs"""

from alembic import op

revision = "d742a3ec9c1b"
down_revision = "aec1985f873e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE run_projection ADD COLUMN run_kind text NOT NULL DEFAULT 'agent' "
        "CHECK (run_kind IN ('agent', 'reviewer'))"
    )


def downgrade() -> None:
    raise NotImplementedError
