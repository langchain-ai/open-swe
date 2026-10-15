"""Job claim token: which worker's run of a job its lease belongs to.

A run finishes, retries or renews its job only under the token it claimed it
with, so a job queued again under its key while it runs is never cleared,
moved or deleted by the run it replaced.
"""

from alembic import op

revision = "6fb9377b47b1"
down_revision = "913a5deda4f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE job ADD COLUMN claim_token uuid")


def downgrade() -> None:
    raise NotImplementedError
