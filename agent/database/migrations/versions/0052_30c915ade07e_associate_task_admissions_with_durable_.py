"""Associate task admissions with execution owners"""

from alembic import op

revision = "30c915ade07e"
down_revision = "6803741a06a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_tool_admission ADD COLUMN owner_id uuid")


def downgrade() -> None:
    raise NotImplementedError
