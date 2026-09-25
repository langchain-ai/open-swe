"""Store the human input summary on the walkthrough"""

from alembic import op

revision = "d66151b3273b"
down_revision = "0a80973775d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE pull_request_walkthrough
        ADD COLUMN human_input_summary text NOT NULL DEFAULT ''
        """
    )
    op.execute("DROP TABLE pull_request_guidance")
    op.execute("DROP TABLE pull_request_guidance_review")


def downgrade() -> None:
    raise NotImplementedError
