"""Task event display metadata"""

from alembic import op

revision = "a823229a905f"
down_revision = "179eb756b3cf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE event_match ADD COLUMN task_event jsonb")


def downgrade() -> None:
    raise NotImplementedError
