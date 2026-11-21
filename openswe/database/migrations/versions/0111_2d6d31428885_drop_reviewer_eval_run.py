"""Drop reviewer_eval_run.

Reviewer evals now run from GitHub workflows instead of the dashboard, so
nothing reads or writes the eval runs it launched.
"""

from alembic import op

revision = "2d6d31428885"
down_revision = "c31a68ec3d5c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reviewer_eval_run")


def downgrade() -> None:
    raise NotImplementedError
