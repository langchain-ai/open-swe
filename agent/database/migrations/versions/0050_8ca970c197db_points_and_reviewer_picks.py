"""Points ledger and pending reviewer picks"""

from alembic import op

revision = "8ca970c197db"
down_revision = ["1a27b64154a3", "52fab62a7608", "243390dbd39e", "f7b59c5091a8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE point (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            delta integer NOT NULL CHECK (delta <> 0),
            reason text NOT NULL,
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX point_created_at ON point (created_at)")
    op.execute("CREATE INDEX point_user_id ON point (user_id)")
    op.execute(
        """
        CREATE UNIQUE INDEX point_one_review_per_pull_request
            ON point (user_id, (details ->> 'repository'), (details ->> 'pr_number'))
            WHERE reason = 'reviewed'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX point_one_per_missed_pick
            ON point (user_id, (details ->> 'request_id'))
            WHERE reason = 'pick_expired'
        """
    )
    op.execute(
        "ALTER TABLE human_review_participant DROP CONSTRAINT human_review_participant_decision_check"
    )
    op.execute(
        """
        ALTER TABLE human_review_participant
            ADD CONSTRAINT human_review_participant_decision_check
                CHECK (decision IN ('approve', 'reject', 'review', 'picked'))
        """
    )


def downgrade() -> None:
    raise NotImplementedError
