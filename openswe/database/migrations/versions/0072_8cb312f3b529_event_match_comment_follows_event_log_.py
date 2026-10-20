"""Event match comment follows event log retention"""

from alembic import op

revision = "8cb312f3b529"
down_revision = ("8272dd05ca91", "11b8f4073839")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "COMMENT ON TABLE event_match IS 'Events owed to a thread. A match is delivered once a "
        "message in the thread state carries its id; matches older than the event_log "
        "retention window are dropped.'"
    )


def downgrade() -> None:
    raise NotImplementedError
