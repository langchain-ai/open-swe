"""Track desktop app use"""

from alembic import op

revision = "53b42abdbed7"
down_revision = ["c5067775584b", "2e0eb2690dd8", "4a8f25fefeb8", "981024b5de47"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE users ADD COLUMN desktop_seen_at timestamptz")


def downgrade() -> None:
    raise NotImplementedError
