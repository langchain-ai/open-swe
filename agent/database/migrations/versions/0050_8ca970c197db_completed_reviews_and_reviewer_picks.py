"""Completed reviews and pending reviewer picks"""

from alembic import op

revision = "8ca970c197db"
down_revision = ["1a27b64154a3", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE completed_review (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            repository_key text NOT NULL,
            pr_number integer NOT NULL CHECK (pr_number > 0),
            reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (user_id, repository_key, pr_number)
        )
        """
    )
    op.execute("CREATE INDEX completed_review_reviewed_at ON completed_review (reviewed_at)")
    op.execute(
        "ALTER TABLE human_review_participant DROP CONSTRAINT human_review_participant_decision_check"
    )
    op.execute(
        """
        ALTER TABLE human_review_participant
            ADD CONSTRAINT human_review_participant_decision_check
                CHECK (decision IN ('approve', 'reject', 'review', 'picked', 'expired'))
        """
    )


def downgrade() -> None:
    raise NotImplementedError
