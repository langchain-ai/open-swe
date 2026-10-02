"""Review points ledger and pending reviewer picks"""

from alembic import op

revision = "8ca970c197db"
down_revision = ["1a27b64154a3", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE review_point (
            id uuid PRIMARY KEY,
            github_id bigint NOT NULL,
            github_login text NOT NULL,
            delta integer NOT NULL CHECK (delta <> 0),
            reason text NOT NULL,
            repository_key text NOT NULL,
            pr_number integer NOT NULL CHECK (pr_number > 0),
            request_id uuid REFERENCES human_review_request (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX review_point_created_at ON review_point (created_at)")
    op.execute(
        """
        CREATE UNIQUE INDEX review_point_one_per_pull_request
            ON review_point (github_id, repository_key, pr_number)
            WHERE reason = 'reviewed'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX review_point_one_per_missed_pick
            ON review_point (github_id, request_id)
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
