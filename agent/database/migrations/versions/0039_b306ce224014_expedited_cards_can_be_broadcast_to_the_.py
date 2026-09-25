"""Expedited cards can be broadcast to the channel"""

from alembic import op

revision = "b306ce224014"
down_revision = "0a80973775d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE expedited_approval ADD COLUMN slack_broadcast boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    raise NotImplementedError
