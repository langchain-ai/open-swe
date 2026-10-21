"""Task delegation start and silent stop.

``created_at`` dates a delegation, so a worker that never starts can be given
up on. ``silenced`` marks a worker cancelled because a person stopped the
task: it owes its coordinator nothing, so its late reports are dropped rather
than waking the coordinator.
"""

from alembic import op

revision = "fd87e6781048"
down_revision = "c5a6e6ef23fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE task_delegation
            ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            ADD COLUMN IF NOT EXISTS silenced boolean NOT NULL DEFAULT false,
            DROP CONSTRAINT IF EXISTS task_delegation_silenced_cancelled,
            ADD CONSTRAINT task_delegation_silenced_cancelled CHECK (NOT silenced OR cancelled)
        """
    )


def downgrade() -> None:
    raise NotImplementedError
