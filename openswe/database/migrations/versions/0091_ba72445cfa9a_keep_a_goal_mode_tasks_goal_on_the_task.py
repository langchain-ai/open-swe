"""Keep a Goal mode task's goal on the task.

Every agent thread coordinates a task from its next run on. Goal mode keeps where a
task's work stands there: its state, why it waits or paused, what it asked its person
for, what will wake it, and who last moved it.
"""

from alembic import op

revision = "ba72445cfa9a"
down_revision = ["1850da6129ee", "3c9e71d2a4b8"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE task
            ADD COLUMN goal_state text,
            ADD COLUMN goal_reason text,
            ADD COLUMN goal_ask text,
            ADD COLUMN goal_waits jsonb,
            ADD COLUMN goal_updated_at timestamptz,
            ADD COLUMN goal_updated_by uuid REFERENCES users (id) ON DELETE SET NULL
    """)


def downgrade() -> None:
    raise NotImplementedError
