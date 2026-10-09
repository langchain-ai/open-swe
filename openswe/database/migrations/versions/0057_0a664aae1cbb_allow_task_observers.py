"""Allow task observers"""

from alembic import op

revision = "0a664aae1cbb"
down_revision = ["9ec28aa0b2a8", "b438344a06b7"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_membership DROP CONSTRAINT task_membership_role_check")
    op.execute("""
        ALTER TABLE task_membership ADD CONSTRAINT task_membership_role_check
            CHECK (role IN ('coordinator', 'worker', 'observer'))
    """)


def downgrade() -> None:
    raise NotImplementedError
