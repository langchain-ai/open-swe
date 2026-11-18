"""Track merge queue recovery pause ownership"""

from alembic import op

revision = "f7b177302414"
down_revision = "3d2a1cb2c1bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE merge_queue ADD COLUMN recovery_pause_id uuid
            REFERENCES merge_queue_decision(id) ON DELETE SET NULL
    """)


def downgrade() -> None:
    raise NotImplementedError
