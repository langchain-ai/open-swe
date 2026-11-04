"""Event log automation triggers"""

from alembic import op

revision = "f6f0bace3658"
down_revision = "5fa6cdee053a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE automation_trigger DROP CONSTRAINT automation_trigger_kind_check")
    op.execute(
        "ALTER TABLE automation_trigger ADD CONSTRAINT automation_trigger_kind_check "
        "CHECK (kind IN ('schedule', 'github', 'slack', 'linear', 'event'))"
    )


def downgrade() -> None:
    raise NotImplementedError
