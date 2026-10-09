"""The event log also holds Open SWE's own decisions"""

from alembic import op

revision = "ba23c09c195d"
down_revision = ["6f06bbadfc02", "a823229a905f"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "COMMENT ON COLUMN event_log.source IS 'Sending service: github, slack, linear, "
        "deployment; or openswe for a decision Open SWE made, such as "
        "human_review.reviewers_released.'"
    )


def downgrade() -> None:
    raise NotImplementedError
