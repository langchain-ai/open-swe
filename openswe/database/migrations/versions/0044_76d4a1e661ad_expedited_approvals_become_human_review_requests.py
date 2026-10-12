"""Expedited approvals become one kind of human review request

Existing rows keep their ids and become ``kind = 'expedited'``; a ``standard``
request is a card in the repository's review channel that people sign up to review.
"""

from alembic import op

revision = "76d4a1e661ad"
down_revision = "b652546da9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE expedited_approval RENAME TO human_review_request")
    op.execute(
        "ALTER TABLE human_review_request "
        "RENAME CONSTRAINT expedited_approval_state_check TO human_review_request_state_check"
    )
    op.execute("ALTER INDEX expedited_approval_active_idx RENAME TO human_review_request_open_idx")
    op.execute(
        """
        ALTER TABLE human_review_request
            ADD COLUMN kind text NOT NULL DEFAULT 'expedited'
                CHECK (kind IN ('expedited', 'standard')),
            ADD COLUMN requested_by_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
            ADD COLUMN tldr text NOT NULL DEFAULT ''
        """
    )
    op.execute("ALTER TABLE human_review_request ALTER COLUMN kind DROP DEFAULT")

    op.execute("ALTER TABLE expedited_approval_vote RENAME TO human_review_participant")
    op.execute("ALTER TABLE human_review_participant RENAME COLUMN approval_id TO request_id")
    op.execute("ALTER TABLE human_review_participant RENAME COLUMN voter_user_id TO user_id")
    op.execute("ALTER TABLE human_review_participant RENAME COLUMN voted_at TO joined_at")
    op.execute(
        "ALTER TABLE human_review_participant DROP CONSTRAINT expedited_approval_vote_decision_check"
    )
    op.execute(
        """
        ALTER TABLE human_review_participant
            ADD CONSTRAINT human_review_participant_decision_check
                CHECK (decision IN ('approve', 'reject', 'review')),
            ADD COLUMN assigned_by_agent boolean NOT NULL DEFAULT false
        """
    )


def downgrade() -> None:
    raise NotImplementedError
