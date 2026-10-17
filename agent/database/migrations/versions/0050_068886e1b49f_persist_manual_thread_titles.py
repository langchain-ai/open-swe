"""Persist manual thread titles"""

from alembic import op

revision = "068886e1b49f"
down_revision = ["1a27b64154a3", "243390dbd39e", "52fab62a7608"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE thread_title_override (
            thread_id text PRIMARY KEY,
            title text NOT NULL CHECK (length(btrim(title)) > 0)
        )
    """)
    op.execute("""
        INSERT INTO thread_title_override (thread_id, title)
        SELECT thread_id, title FROM thread
        WHERE metadata @> '{"title_locked": true}'::jsonb AND length(btrim(title)) > 0
    """)


def downgrade() -> None:
    raise NotImplementedError
