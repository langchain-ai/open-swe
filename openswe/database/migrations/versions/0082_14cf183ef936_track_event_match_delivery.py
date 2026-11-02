"""Track event match delivery"""

from alembic import op

revision = "14cf183ef936"
down_revision = ["b48266ec5989", "a887e76ff00e"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE event_match ADD COLUMN delivered_at timestamptz")
    # Delivery used to be read from each thread's conversation, which already holds
    # these: only events matched from now on start out owed.
    op.execute("UPDATE event_match SET delivered_at = matched_at")


def downgrade() -> None:
    raise NotImplementedError
