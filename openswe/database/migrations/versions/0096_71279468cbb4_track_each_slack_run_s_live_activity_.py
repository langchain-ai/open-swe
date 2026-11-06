"""Track each Slack run's live activity message"""

from alembic import op

revision = "71279468cbb4"
down_revision = "655a0b3fb348"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The message a run keeps updating with its latest work, which its final reply replaces.
    op.execute("ALTER TABLE slack_run ADD COLUMN live_message_ts text")


def downgrade() -> None:
    raise NotImplementedError
