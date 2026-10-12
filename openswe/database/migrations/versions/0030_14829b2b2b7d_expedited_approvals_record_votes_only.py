"""Expedited approvals record votes only"""

from alembic import op

revision = "14829b2b2b7d"
down_revision = "4f14a5ad381a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE expedited_approval DROP CONSTRAINT expedited_approval_state_check")
    op.execute(
        """
        UPDATE expedited_approval
        SET state = 'cancelled', detail = 'Cancelled; ask for a new expedited review.'
        WHERE state IN ('waiting', 'merging', 'failed')
        """
    )
    op.execute(
        """
        ALTER TABLE expedited_approval ADD CONSTRAINT expedited_approval_state_check
            CHECK (state IN ('open', 'merged', 'rejected', 'superseded', 'cancelled'))
        """
    )
    op.execute("DROP INDEX expedited_approval_active_idx")
    op.execute(
        """
        CREATE UNIQUE INDEX expedited_approval_active_idx
            ON expedited_approval (pull_request_id)
            WHERE state = 'open'
        """
    )
    op.execute("ALTER TABLE expedited_approval DROP COLUMN cron_id")
    op.execute("ALTER TABLE expedited_approval DROP COLUMN advisory_failures")

    op.execute(
        "ALTER TABLE expedited_approval_vote ADD COLUMN github_review_sha text NOT NULL DEFAULT ''"
    )
    op.execute(
        """
        UPDATE expedited_approval_vote AS vote
        SET github_review_sha = approval.head_sha
        FROM expedited_approval AS approval
        WHERE vote.approval_id = approval.id AND vote.github_review_id IS NOT NULL
        """
    )


def downgrade() -> None:
    raise NotImplementedError
