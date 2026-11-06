"""Keep a Goal mode task's goal on the task."""

from alembic import op

revision = "ba72445cfa9a"
down_revision = "655a0b3fb348"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE task
            ADD COLUMN goal_objective text,
            ADD COLUMN goal_state text,
            ADD COLUMN goal_reason text,
            ADD COLUMN goal_ask text,
            ADD COLUMN goal_waits jsonb,
            ADD COLUMN goal_updated_at timestamptz,
            ADD COLUMN goal_updated_by uuid REFERENCES users (id) ON DELETE SET NULL
    """)


def downgrade() -> None:
    raise NotImplementedError
