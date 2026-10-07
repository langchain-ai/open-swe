"""Repair databases that applied a draft of the reviewer picks migration"""

from alembic import op

revision = "6f06bbadfc02"
down_revision = ["8ca970c197db", "243390dbd39e", "f7b59c5091a8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preview and dev databases ran earlier drafts of 8ca970c197db under the same id.
    op.execute("DROP TABLE IF EXISTS review_point")
    op.execute("DROP TABLE IF EXISTS point")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS completed_review (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            repository_key text NOT NULL,
            pr_number integer NOT NULL CHECK (pr_number > 0),
            reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (user_id, repository_key, pr_number)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS completed_review_reviewed_at ON completed_review (reviewed_at)"
    )
    op.execute(
        "ALTER TABLE human_review_participant "
        "DROP CONSTRAINT IF EXISTS human_review_participant_decision_check"
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
