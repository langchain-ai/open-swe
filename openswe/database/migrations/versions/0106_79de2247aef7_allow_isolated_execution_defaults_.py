"""Allow isolated execution defaults settings"""

from alembic import op

revision = "79de2247aef7"
down_revision = "3d2a1cb2c1bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '1min'")
    op.execute("""
        ALTER TABLE settings_record
            DROP CONSTRAINT IF EXISTS settings_record_tier_check,
            ADD CONSTRAINT settings_record_tier_check
                CHECK (tier IN ('instance', 'workspace', 'execution'))
    """)


def downgrade() -> None:
    raise NotImplementedError
