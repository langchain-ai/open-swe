"""Canonicalize legacy event subscription sources"""

from alembic import op
from sqlalchemy import text

revision = "5123042028cf"
down_revision = "d9d2e51c4a9e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '1min'")
    op.execute(
        text("""
            UPDATE event_subscription
            SET sources = array_replace(sources, :legacy_source, :source)
            WHERE sources @> ARRAY[:legacy_source]::text[]
        """).bindparams(legacy_source="workspace", source="robin")
    )


def downgrade() -> None:
    raise NotImplementedError
