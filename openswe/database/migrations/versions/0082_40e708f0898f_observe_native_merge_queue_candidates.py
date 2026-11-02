"""Observe native merge queue candidates"""

from alembic import op

revision = "40e708f0898f"
down_revision = ["b48266ec5989", "a887e76ff00e"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE merge_queue
        ADD COLUMN mode text NOT NULL DEFAULT 'custom' CHECK (mode IN ('custom', 'native')),
        ADD COLUMN native_snapshot jsonb
    """)


def downgrade() -> None:
    raise NotImplementedError
