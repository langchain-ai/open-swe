"""Let the curator supersede a memory.

A memory the curator marks ``superseded`` is fully covered by a line it wrote in the
workspace's prompt, so it leaves its person's own memories wherever they read that prompt.
"""

from alembic import op

revision = "5f101256cbc0"
down_revision = "c664a63ef3bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE memory DROP CONSTRAINT memory_status_check")
    op.execute(
        "ALTER TABLE memory ADD CONSTRAINT memory_status_check "
        "CHECK (status IN ('pending', 'held', 'adopted', 'superseded', 'declined')) NOT VALID"
    )
    op.execute("ALTER TABLE memory VALIDATE CONSTRAINT memory_status_check")
    op.execute(
        "COMMENT ON COLUMN memory.status IS 'The curator''s decision: pending until it decides; "
        "adopted when the workspace''s prompt draws on it; superseded when a line there fully "
        "covers it, so it leaves its person''s memories wherever they read that prompt; held "
        "when saved in a private thread before every memory reached the curator, so it never "
        "reads or decides it.'"
    )


def downgrade() -> None:
    raise NotImplementedError
