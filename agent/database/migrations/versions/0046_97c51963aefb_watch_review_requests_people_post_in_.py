"""Watch review requests people post in Slack

A ``posted`` request is someone's own message in a review channel: Open SWE reacts
to it when the pull request is approved or merged and bumps it once it has sat green
and unapproved.
"""

from alembic import op

revision = "97c51963aefb"
down_revision = "84e4e97b6fec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE human_review_request
            DROP CONSTRAINT human_review_request_kind_check,
            ADD CONSTRAINT human_review_request_kind_check
                CHECK (kind IN ('expedited', 'standard', 'posted')),
            ADD COLUMN approved_at timestamptz,
            ADD COLUMN ready_since timestamptz
        """
    )


def downgrade() -> None:
    raise NotImplementedError
